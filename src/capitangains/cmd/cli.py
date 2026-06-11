"""Command-line entry point for the Portugal capital-gains report.

This module is the interface layer. It defines the argument parser, configures logging
from the verbosity flags, translates the parsed arguments into a ``RunOptions`` value,
and hands off to ``capitangains.pipeline.run``, which owns the actual orchestration.
Keeping this layer thin is deliberate: the pipeline consumes an explicit options object
rather than ``argparse`` output, so it can be driven without the CLI.

Usage
-----
    # Single year input
    capitangains --year 2024 --output ./out.xlsx --fx-table ./fx_rates.csv \
        /path/to/ActivityStatement_2024.csv

    # Multi-year input (include prior years so FIFO has buys)
    capitangains --year 2024 --output ./out.xlsx --fx-table ./fx_rates.csv \
        /path/ActivityStatement_2023.csv /path/ActivityStatement_2024.csv

    # Auto-discover statements in a directory (selects this year's and prior years',
    # ignores non-statement csv); mutually exclusive with positional path(s)
    capitangains --year 2024 --output ./out.xlsx --fx-table ./fx_rates.csv \
        --statements-dir /path/to/statements

    # Dry run: validate everything, write nothing (leaves any existing report intact)
    capitangains --year 2024 --dry-run --fx-table ./fx_rates.csv \
        /path/to/ActivityStatement_2024.csv

(Equivalently, invoke the module with ``python -m capitangains.cmd``.)

Forex CSV schema (base EUR):
    date,currency,rate
    1999-01-04,AUD,1.91
    1999-01-04,GBP,0.7111
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from capitangains.cmd.discovery import (
    DiscoveredStatement,
    DiscoveryResult,
    discover_statements,
)
from capitangains.logging import configure_logging
from capitangains.pipeline import RunOptions, run


def _broker_country(value: str) -> str:
    """Normalize and validate the --broker-country argument to an upper-case 2-letter
    code, so a typo (e.g. a full country name) fails fast at the boundary rather than
    surfacing as a malformed source country on the Quadro 8A interest line.
    """
    cc = value.strip().upper()
    # isascii() guards isalpha()'s Unicode-awareness: a 2-letter accented input would
    # otherwise pass and upper-case into a non-ASCII "country code". This mirrors the
    # [A-Z]{2} the ISIN prefix accepts.
    if len(cc) != 2 or not (cc.isascii() and cc.isalpha()):
        raise argparse.ArgumentTypeError(
            f"expected a 2-letter ISO country code, got {value!r}"
        )
    return cc


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Portugal Capital Gains Report from IBKR Activity Statement CSV"
    )
    p.add_argument(
        "--year", type=int, required=True, help="Calendar year to report (YYYY)"
    )
    p.add_argument(
        "input",
        type=str,
        nargs="*",
        help=(
            "Activity Statement CSV path(s) (include prior years for FIFO). Omit when "
            "using --statements-dir."
        ),
    )
    p.add_argument(
        "--statements-dir",
        type=str,
        default=None,
        metavar="DIR",
        help=(
            "Directory to auto-discover IBKR Activity Statement CSVs in, selecting "
            "those whose reporting period starts in --year or earlier (so FIFO has the "
            "prior-year buys). Non-statement csv files are ignored. Mutually exclusive "
            "with positional path(s)."
        ),
    )
    p.add_argument(
        "--fx-table",
        type=str,
        default=None,
        help=(
            "Forex rates CSV with base EUR: 'date,currency,rate' where "
            "'rate' is target currency units per EUR"
        ),
    )
    p.add_argument(
        "--locale",
        type=str,
        default="EN",
        choices=["EN", "PT"],
        help="Locale for headers and sheet names",
    )
    p.add_argument(
        "--broker-country",
        type=_broker_country,
        default="IE",
        metavar="CC",
        help=(
            "ISO 2-letter jurisdiction of the IBKR contracting entity; source country "
            "for broker-paid interest (credit + SYEP). Default IE (IBKR Ireland)."
        ),
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output filename (e.g., report.xlsx). If omitted, uses report_<year>.xlsx",
    )
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help=(
            "Run the full pipeline -- parse, extract, FIFO-match, convert, reconcile "
            "-- and report any defect, but stop before writing the workbook, leaving "
            "any existing output untouched. Exit 0 if the run would succeed; any "
            "defect is reported and exits non-zero, exactly as a normal run would."
        ),
    )
    p.add_argument(
        "--auto-fix-sell-gaps",
        type=str,
        default=None,
        metavar="SYMBOL@YYYY-MM-DD[,...]",
        help=(
            "Itemized acknowledgment of unmatched SELLs (gaps) whose cost basis you "
            "authorize to be synthesized from IBKR's per-trade Basis. Pass a "
            "comma-separated list of SYMBOL@YYYY-MM-DD keys, one per gap you have "
            "reviewed (symbols are case-sensitive). The run is fatal (exit 2, no "
            "workbook) if any gap is left unlisted, any acknowledged gap has a missing "
            "or corrupt Basis, or any acknowledgment matches no gap found this run."
        ),
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity: -v (INFO), -vv (DEBUG)",
    )
    return p


def _period_label(statement: DiscoveredStatement) -> str:
    """Render a discovered statement's period for the manifest, or flag unreadable."""
    period = statement.period
    if period is None:
        return "identity unreadable"
    return f"{period.start} to {period.end}"


def _print_manifest(result: DiscoveryResult) -> None:
    """Print the discovery manifest to stdout: the three buckets, each with its count.

    All three headers are printed even at count zero, so the operator always sees what
    was and was not picked up. Plain ASCII and deterministic. This is a primary output
    of --statements-dir, so it goes to stdout via print (unconditional; there is no
    quiet flag) -- deliberately separate from the verbosity-gated diagnostic logger.
    """
    print(f"Selected {len(result.selected)} statement(s) to report on:")
    for s in result.selected:
        print(f"  {s.path} ({_period_label(s)})")

    print(f"Excluded {len(result.excluded_future)} statement(s) as future:")
    for s in result.excluded_future:
        print(f"  {s.path} ({_period_label(s)})")

    print(f"Ignored {len(result.ignored)} non-statement file(s):")
    for path in result.ignored:
        print(f"  {path}")


def resolve_inputs(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> list[str]:
    """Resolve the run's input paths from positional args or --statements-dir.

    Exactly one source must be given. The two are hand-validated rather than placed in
    an argparse mutually-exclusive group: a nargs="*" positional defaults to [] (it is
    never "absent"), which defeats the group's presence test. Every violation exits 2
    via ``parser.error``.

    With --statements-dir, the directory is discovered, the manifest is printed to
    stdout, and the selected statements' paths are returned for the pipeline. An empty
    selection is fatal -- there is nothing to report on. Discovery includes any
    identity-unreadable statement (period unknown); the pipeline's identity gate, not
    this resolver, reports it.
    """
    if args.statements_dir is not None and args.input:
        parser.error("pass either statement path(s) or --statements-dir, not both.")
    if args.statements_dir is None and not args.input:
        parser.error(
            "no input: pass one or more statement path(s), or --statements-dir DIR."
        )

    if args.statements_dir is None:
        return list(args.input)

    directory = Path(args.statements_dir)
    if not directory.is_dir():
        parser.error(f"--statements-dir is not a directory: {directory}")

    result = discover_statements(directory, args.year)
    _print_manifest(result)
    if not result.selected:
        parser.error(
            f"no IBKR statements found in {directory} for {args.year} or earlier."
        )
    return [str(s.path) for s in result.selected]


def main() -> None:
    parser = build_argparser()
    args = parser.parse_args()

    # Configure logging based on verbosity
    verbosity_map = {
        0: logging.WARNING,  # Default: quiet
        1: logging.INFO,  # -v: informational
        2: logging.DEBUG,  # -vv and above: debug
    }
    level = verbosity_map.get(min(args.verbose, 2), logging.WARNING)
    configure_logging(level=level)

    inputs = resolve_inputs(args, parser)

    run(
        RunOptions(
            inputs=inputs,
            year=args.year,
            fx_table=args.fx_table,
            locale=args.locale,
            output=args.output,
            auto_fix_sell_gaps=args.auto_fix_sell_gaps,
            dry_run=args.dry_run,
            broker_country=args.broker_country,
        )
    )


if __name__ == "__main__":
    main()
