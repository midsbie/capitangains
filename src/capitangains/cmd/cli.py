"""
Analyze Interactive Brokers (IBKR) Activity Statement CSVs and produce a capital gains
report tailored for Portugal (FIFO, commissions included, EUR outputs when possible).

This module acts as the CLI orchestrator, delegating responsibilities to SRP modules:
- Parsing/Model: capitangains.model
- Data extraction: capitangains.reporting.extract
- FIFO matching: capitangains.reporting.fifo
- FX conversion: capitangains.reporting.fx
- Reconciliation: capitangains.reporting.reconcile
- Output writing: capitangains.reporting.report_builder

Usage
-----
    # Single year input
    python -m capitangains.cmd.generate_ibkr_report \
        --year 2024 \
        --output ./out.xlsx \
        --fx-table ./fx_rates.csv \
        /path/to/ActivityStatement_2024.csv

    # Multi-year input (include prior years so FIFO has buys)
    python -m capitangains.cmd.generate_ibkr_report \
        --year 2024 \
        --output ./out.xlsx \
        --fx-table ./fx_rates.csv \
        /path/ActivityStatement_2023.csv /path/ActivityStatement_2024.csv

    # Dry run: validate everything, write nothing (leaves any existing report intact)
    python -m capitangains.cmd.generate_ibkr_report \
        --year 2024 \
        --dry-run \
        --fx-table ./fx_rates.csv \
        /path/to/ActivityStatement_2024.csv

Forex CSV schema (base EUR):
    date,currency,rate
    1999-01-04,AUD,1.91
    1999-01-04,GBP,0.7111
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from capitangains.conv import parse_date
from capitangains.errors import DataQualityError
from capitangains.logging import configure_logging
from capitangains.model import IbkrStatementCsvParser, merge_models, merge_reports
from capitangains.reporting import (
    ExtractionDefect,
    FifoMatcher,
    FxTable,
    ReportBuilder,
    TradeRow,
    TransferRow,
    parse_dividends,
    parse_interest,
    parse_syep_interest_details,
    parse_trades_stocklike,
    parse_transfers,
    parse_withholding_tax,
    reconcile_realized_against_ibkr,
)
from capitangains.reporting.fifo_domain import GapEvent, GapKey, GapResolution
from capitangains.reporting.gap_policy import build_gap_policy
from capitangains.reporting.report_sink import ExcelReportSink


def validate_symbol_currency_uniqueness(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> None:
    """Enforce one-currency-per-symbol invariant across all extracted events.

    Design choice: IBKR symbols are treated as exchange-specific identifiers, each
    denominated in a single currency.  If the same ticker appears on exchanges with
    different currencies (e.g. "RY" on NYSE/USD and TSX/CAD), the CSV data must
    disambiguate them with distinct symbols.  Allowing multiple currencies per symbol
    would make the per-symbol summary incoherent -- trade-currency columns can only
    represent one denomination, while EUR columns aggregate across all, producing
    rows that cannot be reconciled.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    events: Sequence[TradeRow | TransferRow] = [*trades, *transfers]
    for event in events:
        seen[event.symbol].add(event.currency)
    violations = {sym: ccys for sym, ccys in seen.items() if len(ccys) > 1}
    if not violations:
        return
    details = "\n".join(
        f"  {sym}: {', '.join(sorted(ccys))}"
        for sym, ccys in sorted(violations.items())
    )
    raise DataQualityError(
        f"symbol-currency uniqueness violated -- each symbol must map to exactly "
        f"one trade currency, but the following appear in multiple:\n{details}"
    )


def _event_sort_key(
    event: TradeRow | TransferRow,
) -> tuple[dt.date, str, int]:
    """Order the merged trade/transfer stream for FIFO ingestion.

    Trades sort by their real intraday timestamp (IBKR's Date/Time), with buys before
    sells only as a tie-break for an identical timestamp. Transfers carry no intraday
    time, so they sort by date alone. That is sufficient because
    _report_transfer_ordering_collisions has already aborted the run if any transfer
    shared a (symbol, currency) day with other order-sensitive activity. The only
    same-day pairings that can still reach this sort are in independent symbols, whose
    relative order does not affect FIFO (consumption is keyed per symbol).
    """
    if isinstance(event, TransferRow):
        return (event.date, "", 0)
    elif isinstance(event, TradeRow):
        sub = 0 if event.quantity > 0 else 1
        return (event.date, event.datetime_str, sub)
    raise ValueError(f"unexpected event type: {type(event)}")


def _parse_acknowledged_gaps(spec: str | None) -> frozenset[GapKey]:
    """Parse the operator's itemized gap-acknowledgment spec into a set of keys.

    The spec is a comma-separated list of ``SYMBOL@YYYY-MM-DD`` tokens, each naming one
    unmatched SELL the operator has reviewed and authorized to be valued from IBKR's
    per-trade Basis. Symbols are case-sensitive and compared verbatim (only surrounding
    whitespace is stripped); a single key authorizes every gap sharing that
    ``(symbol, date)``. Empty tokens (from a leading, trailing, or doubled comma) are
    skipped; ``None`` or an all-empty spec yields an empty set -- zero acknowledgments.
    Every malformed token is collected and reported together as one ``DataQualityError``
    so the spec can be fixed in a single pass.
    """
    if spec is None:
        return frozenset()

    keys: set[GapKey] = set()
    malformed: list[str] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        parts = token.split("@")
        if len(parts) != 2:
            malformed.append(token)
            continue
        symbol, date_str = parts[0].strip(), parts[1].strip()
        if not symbol or not date_str:
            malformed.append(token)
            continue
        try:
            key_date = parse_date(date_str)
        except ValueError:
            malformed.append(token)
            continue
        keys.add((symbol, key_date))

    if malformed:
        raise DataQualityError(
            "Malformed --auto-fix-sell-gaps acknowledgment(s) "
            f"(expected SYMBOL@YYYY-MM-DD): {', '.join(malformed)}"
        )
    return frozenset(keys)


def _report_gap_acknowledgments(
    gaps: Sequence[GapEvent],
    acknowledged: frozenset[GapKey],
    logger: logging.Logger,
) -> None:
    """Tie out the gaps found against the operator's acknowledgments, at the boundary.

    A SELL gap is always a real, in-scope disposal that must be reported; the only
    open question is the valuation of its cost basis, and that valuation is never
    auto-applied without explicit, audited sign-off. So this reconciles two ways:

    - Every gap must be acknowledged: an UNACKNOWLEDGED gap is fatal.
    - Every acknowledged gap must have a usable IBKR Basis: a DEFECTIVE one (missing
      Basis, or a Basis its own Realized P/L contradicts) is fatal.
    - Every acknowledgment must match a gap found this run: an orphan acknowledgment
      (stale, mistyped, or for a gap that did not occur) is fatal.

    All three are accumulated into one pre-flight report: one ERROR per offending item,
    a summary ERROR, then a single SystemExit(2) -- no fail-fast, so the operator sees
    every problem in one pass. Only when the tie-out is clean does each SYNTHESIZED gap
    emit its per-lot audit WARNING (the synthetic cost basis is never silent).
    """
    if not gaps and not acknowledged:
        return

    gap_keys = {(ge.symbol, ge.date) for ge in gaps}
    orphans = sorted(acknowledged - gap_keys)
    unacknowledged = [ge for ge in gaps if ge.outcome is GapResolution.UNACKNOWLEDGED]
    defective = [ge for ge in gaps if ge.outcome is GapResolution.DEFECTIVE]

    if unacknowledged or defective or orphans:
        for ge in unacknowledged:
            logger.error(
                "Unacknowledged SELL gap: symbol=%s date=%s qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        for ge in defective:
            logger.error(
                "Acknowledged SELL gap has a defective IBKR Basis: symbol=%s date=%s "
                "qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        for sym, day in orphans:
            logger.error(
                "Orphan acknowledgment: %s@%s matched no SELL gap this run "
                "(stale, mistyped, or for a gap that did not occur).",
                sym,
                day,
            )
        logger.error(
            "Gap acknowledgment tie-out failed: %d unacknowledged gap(s), %d "
            "acknowledged gap(s) with a defective Basis, %d orphan acknowledgment(s). "
            "Acknowledge each reviewed gap explicitly with "
            '--auto-fix-sell-gaps "SYMBOL@YYYY-MM-DD[,...]" (only after verifying its '
            "IBKR Basis), and drop any acknowledgment that matches no gap.",
            len(unacknowledged),
            len(defective),
            len(orphans),
        )
        raise SystemExit(2)

    for ge in gaps:
        if ge.outcome is GapResolution.SYNTHESIZED:
            logger.warning(
                "Synthesized residual lot for unmatched SELL -- basis taken from IBKR "
                "Basis, not independently verified: symbol=%s date=%s qty=%s "
                "currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )


def _report_extraction_defects(
    defects: Sequence[ExtractionDefect], logger: logging.Logger
) -> None:
    """Abort if any extraction row was rejected, listing every defect first.

    Each extractor accumulates its row-level data-quality defects instead of raising on
    the first bad row, so the operator sees every problem in one pass (mirroring the FX
    and gap-acknowledgment reports above). One ERROR per defect, a summary ERROR, then a
    single SystemExit(2) -- no workbook written.
    """
    if not defects:
        return

    for d in defects:
        logger.error(
            "Row rejected in %s (symbol=%s, date=%s): %s",
            d.section,
            d.symbol or "n/a",
            d.date or "n/a",
            d.reason,
        )

    logger.error(
        "Extraction rejected %d row(s); no workbook written. Correct the offending "
        "row(s) above and rerun.",
        len(defects),
    )
    raise SystemExit(2)


def _report_missing_fx(
    missing: set[tuple[dt.date, str]], logger: logging.Logger
) -> None:
    """Abort if the FX table could not supply every rate the EUR report needs.

    A complete table is a precondition: substituting another date's rate would silently
    misstate cost basis or proceeds, and blank EUR cells would understate totals. List
    every missing (date, currency) so the table can be completed in one pass, then exit
    2 without writing a workbook.
    """
    if not missing:
        return

    for d, ccy in sorted(missing):
        logger.error("Missing FX rate: %s on %s", ccy, d)

    logger.error(
        "EUR conversion incomplete: %d required FX rate(s) absent from the table; "
        "no workbook written. Add the rate(s) above (or widen the table's date range) "
        "and rerun.",
        len(missing),
    )
    raise SystemExit(2)


def _report_transfer_ordering_collisions(
    trades: Sequence[TradeRow],
    transfers: Sequence[TransferRow],
    logger: logging.Logger,
) -> None:
    """Abort if a transfer shares a symbol's calendar day with other activity.

    Why this is fatal rather than resolved. FIFO lot creation and consumption are
    order-sensitive and keyed by (symbol, currency): the sequence in which buys, sells,
    transfer-ins (which seed a lot) and transfer-outs (which consume lots) are ingested
    decides which lots a disposal matches, and therefore its cost basis and realized
    P/L. IBKR's Trades section carries a full intraday timestamp (Date/Time), but its
    Transfers section carries only a Date -- no time, and the Code does not encode one
    -- so when a transfer lands on the same day as other order-sensitive activity in the
    same symbol, their true intraday order is simply not in the data. There is no honest
    way to deduce it.

    The earlier implementation papered over this with a fabricated convention
    (transfer-in before all same-day trades, transfer-out after) encoded as priority
    constants in the sort key. That silently risked a wrong cost basis on exactly the
    figures this tool exists to get right, with no signal to the operator. Because a tax
    figure must be correct rather than plausibly guessed (the stance already taken for a
    missing FX rate or an unmatched sell) we refuse to assume an order IBKR did not
    provide, and halt so the operator can resolve the affected symbol against their own
    records.

    Why halting (and building no mitigation) is acceptable. The collision is rare to the
    point of being an edge case: a transfer is a position migration, whose normal
    lifecycle is migrate-then-hold (or migrate-then-trade-on-a-later-day), and
    receiving-broker settlement windows push a transfer and same-symbol trading apart.
    No convention or override mechanism is therefore built; this guard only states the
    cause when the rare case occurs, preserving today's findings for future readers.

    Scope. Only same (symbol, currency) matters, since consumption is keyed that way; a
    transfer sharing a day with unrelated symbols is independent and stays silent. A
    transfer colliding with another transfer of the same symbol on the same day is
    equally unorderable and is caught here too. Every collision is listed, then a single
    SystemExit(2), and a workbook is not written.
    """
    trades_by_key: dict[tuple[str, str, dt.date], int] = defaultdict(int)
    for t in trades:
        trades_by_key[(t.symbol, t.currency, t.date)] += 1

    transfers_by_key: dict[tuple[str, str, dt.date], int] = defaultdict(int)
    for tr in transfers:
        transfers_by_key[(tr.symbol, tr.currency, tr.date)] += 1

    # A transfer collides when its (symbol, currency, date) also holds a trade, or a
    # second transfer of the same symbol that day -- either way the intraday order is
    # undetermined.
    collisions = [
        (key, n_xfer, trades_by_key.get(key, 0))
        for key, n_xfer in transfers_by_key.items()
        if trades_by_key.get(key, 0) > 0 or n_xfer > 1
    ]
    if not collisions:
        return

    for (symbol, currency, date), n_xfer, n_trade in sorted(collisions):
        logger.error(
            "Unorderable same-day events for %s (%s) on %s: %d transfer(s) and %d "
            "trade(s) share the date, but IBKR gives transfers no intraday time, so "
            "their FIFO order cannot be determined.",
            symbol,
            currency,
            date,
            n_xfer,
            n_trade,
        )

    logger.error(
        "Transfer ordering is ambiguous for %d symbol-day(s); no workbook written. "
        "IBKR does not timestamp transfers, so a transfer landing on the same day as "
        "other activity in the same symbol cannot be ordered against it, and the cost "
        "basis would depend on an assumption the data does not support. Resolve the "
        "affected symbol(s) by hand, or adjust the inputs so the transfer and the "
        "same-day activity do not coincide.",
        len(collisions),
    )
    raise SystemExit(2)


def process_files(args: argparse.Namespace) -> None:
    # Get logger for this module
    logger = logging.getLogger(__name__)

    # Parse the gap-acknowledgment spec before any file I/O so a malformed spec fails
    # fast (exit 2) without touching the statements.
    try:
        acknowledged = _parse_acknowledged_gaps(
            getattr(args, "auto_fix_sell_gaps", None)
        )
    except DataQualityError as e:
        logger.error("%s", e)
        raise SystemExit(2) from e

    # Parse one or more CSVs
    inputs = args.input if isinstance(args.input, list) else [args.input]
    logger.info("Reading %d file(s): %s", len(inputs), ", ".join(inputs))

    parser = IbkrStatementCsvParser()
    models = []
    reports = []
    for p in inputs:
        m, rep = parser.parse_file(p)
        logger.debug(
            "Parsed %s: %d sections, %d subtables",
            p,
            len(m.sections),
            sum(len(subs) for subs in m.sections.values()),
        )
        models.append(m)
        reports.append(rep)

    model = merge_models(models)
    parse_report = merge_reports(reports)
    parse_report.log_with(logger)
    if parse_report.has_errors:
        raise SystemExit(2)

    # Each extractor accumulates its row-level data-quality defects rather than failing
    # on the first bad row, so a single run surfaces every rejected row. Concatenate the
    # six defect lists and halt once at the boundary (exit 2, no workbook) if any are
    # present -- consistent with the parse-error abort above.
    trades, trade_defects = parse_trades_stocklike(model, asset_scope="stocks_etfs")
    transfers, transfer_defects = parse_transfers(model)
    dividends, dividend_defects = parse_dividends(model)
    withholding, withholding_defects = parse_withholding_tax(model)
    syep_interest, syep_defects = parse_syep_interest_details(model)
    interest, interest_defects = parse_interest(model)

    _report_extraction_defects(
        [
            *trade_defects,
            *transfer_defects,
            *dividend_defects,
            *withholding_defects,
            *syep_defects,
            *interest_defects,
        ],
        logger,
    )

    logger.info(
        "Extracted: %d trades, %d dividends, %d withholding, %d interest, %d transfers",
        len(trades),
        len(dividends),
        len(withholding),
        len(interest),
        len(transfers),
    )

    # Cross-row invariant (not per-row): raises one aggregated DataQualityError,
    # translated to a clean exit 2 here. The per-row extraction defects above are
    # already handled.
    try:
        validate_symbol_currency_uniqueness(trades, transfers)
    except DataQualityError as e:
        logger.error("%s", e)
        raise SystemExit(2) from e

    # A transfer carries only a date (IBKR gives transfers no intraday time), so a
    # transfer sharing a symbol's day with other order-sensitive activity cannot be
    # ordered for FIFO. Halt rather than guess -- see the helper's rationale.
    _report_transfer_ordering_collisions(trades, transfers, logger)

    # Build FIFO realized. The composition root owns gap-policy assembly: the matcher
    # itself stays agnostic of how gaps are resolved.
    matcher = FifoMatcher(gap_policy=build_gap_policy(acknowledged))

    # Merge trades and transfers into a single chronological stream so that FIFO lot
    # creation/consumption respects actual event ordering. Same-day same-symbol transfer
    # collisions were already rejected above, so the sort key needs no fabricated
    # transfer-vs-trade tie-break (see _event_sort_key).
    events: list[TradeRow | TransferRow] = [*trades, *transfers]
    events.sort(key=_event_sort_key)

    realized = []
    for event in events:
        if isinstance(event, TransferRow):
            matcher.ingest_transfer(event)
            continue
        elif not isinstance(event, TradeRow):
            raise ValueError(f"unexpected event type in merged stream: {type(event)}")

        rl = matcher.ingest_trade(event)
        if rl is not None:  # keep only realized lines generated from sells
            realized.append(rl)

    logger.info(
        "FIFO matching: %d trades processed, %d realized lines generated",
        len(trades),
        len(realized),
    )

    _report_gap_acknowledgments(matcher.gap_events, acknowledged, logger)

    # Build report
    rb = ReportBuilder(year=args.year)
    for rl in realized:
        if rl.sell_date.year == args.year:
            rb.add_realized(rl)
    rb.set_dividends([d for d in dividends if d.date.year == args.year])
    rb.set_withholding([w for w in withholding if w.date.year == args.year])

    # Keep only rows with a value date in the selected year (drop CSV 'Total' lines)
    rb.set_syep_interest(
        [r for r in syep_interest if r.value_date and r.value_date.year == args.year]
    )
    rb.set_interest([i for i in interest if i.date.year == args.year])
    rb.set_transfers(transfers)  # Include all transfers, not filtered by year

    logger.info(
        "Report built: %d realized lines, %d dividend lines, %d withholding lines",
        len(rb.realized_lines),
        len(rb.dividends),
        len(rb.withholding),
    )

    # FX conversion if provided
    fx: FxTable | None = None
    if args.fx_table:
        try:
            fx = FxTable.from_csv(args.fx_table)
        except Exception as e:
            # A missing or unparseable FX table is a setup failure, not a defect in the
            # statement data. It exits 1, a class apart from the curated gates' exit 2,
            # and is surfaced as one clean ERROR rather than a raw crash.
            logger.error(
                "Failed to prepare FX conversion from %s: %s", args.fx_table, e
            )
            raise SystemExit(1) from e

    rb.convert_eur(fx)
    _report_missing_fx(rb.fx_missing, logger)

    # Soft reconciliation: cross-check our realized P/L against IBKR's own per-trade
    # `Realized P/L`, per symbol, in each instrument's trade currency. No FX stands
    # between the two sides, so a disagreement beyond cent rounding is a real accounting
    # gap rather than a rate artifact. Single-file only: IBKR's per-statement realized
    # column cannot be meaningfully summed across periods.
    if len(inputs) == 1:
        try:
            report = reconcile_realized_against_ibkr(
                trades, rb.realized_lines, args.year
            )
            for r in report.reconciled:
                logger.debug(
                    "Reconciliation: %s (%s) - mine: %s, IBKR: %s, diff: %s (%s)",
                    r.symbol,
                    r.currency,
                    r.computed,
                    r.ibkr,
                    r.diff,
                    "OK" if r.is_match else "MISMATCH",
                )
            mismatches = [r for r in report.reconciled if not r.is_match]
            if mismatches:
                logger.warning(
                    "Reconciliation: %d symbol(s) disagree with IBKR realized P/L "
                    "[symbol, currency, mine, IBKR]: %s",
                    len(mismatches),
                    [
                        (r.symbol, r.currency, r.computed, r.ibkr)
                        for r in mismatches[:10]
                    ],
                )
            if report.synthetic:
                # A synthesized line agrees with IBKR by construction, so report these
                # separately: a green reconciliation must not read as independent
                # confirmation. The figures still matter since, where a symbol mixes
                # synthesized and genuine sells, the diff tracks the genuine portion, so
                # a large diff here is a real gap, not a tautology.
                logger.info(
                    "Reconciliation: %d symbol(s) carry synthesized basis -- not "
                    "independently confirmed [symbol, currency, mine, IBKR, diff]: %s",
                    len(report.synthetic),
                    [
                        (r.symbol, r.currency, r.computed, r.ibkr, r.diff)
                        for r in report.synthetic[:10]
                    ],
                )
        except Exception:
            logger.exception("Reconciliation failed; continuing without it.")
    else:
        logger.info(
            "Skipping IBKR realized-P/L reconciliation for multi-file input "
            "(spans multiple periods)."
        )

    # Determine output path
    out_path = Path(args.output) if args.output else Path(f"report_{args.year}.xlsx")

    # The workbook write is this program's only side effect; everything above is pure
    # validation and computation, and every abort path precedes it. A dry run performs
    # that full preflight and stops here, so an existing report at out_path is left
    # untouched. (It cannot exercise the write stage itself -- serialization, path
    # permissions, disk.)
    if args.dry_run:
        logger.info(
            "Dry run: all checks passed; no workbook written (would write to %s).",
            out_path,
        )
        return

    # Write outputs via sink
    sink = ExcelReportSink(out_path=out_path, locale=args.locale)
    out_path = sink.write(rb)
    logger.info("Wrote workbook to %s", out_path)


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

    process_files(args)


if __name__ == "__main__":
    main()
