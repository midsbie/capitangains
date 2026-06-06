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

from capitangains.logging import configure_logging
from capitangains.pipeline import RunOptions, run


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
        nargs="+",
        help="One or more Activity Statement CSV paths (include prior years for FIFO)",
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

    run(
        RunOptions(
            inputs=args.input,
            year=args.year,
            fx_table=args.fx_table,
            locale=args.locale,
            output=args.output,
            auto_fix_sell_gaps=args.auto_fix_sell_gaps,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
