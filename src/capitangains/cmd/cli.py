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

from capitangains.errors import DataQualityError
from capitangains.logging import configure_logging
from capitangains.model import IbkrStatementCsvParser, merge_models, merge_reports
from capitangains.reporting import (
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
from capitangains.reporting.fifo_domain import GapEvent
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
) -> tuple[dt.date, int, str, int]:
    if isinstance(event, TransferRow):
        direction = event.direction.strip().lower()
        priority = 0 if direction == "in" else 2
        return (event.date, priority, "", 0)
    elif isinstance(event, TradeRow):
        # Buys before sells only as tie-break for identical timestamps.
        sub = 0 if event.quantity > 0 else 1
        return (event.date, 1, event.datetime_str, sub)
    raise ValueError(f"unexpected event type: {type(event)}")


def _report_sell_gaps(
    gaps: Sequence[GapEvent], fix_sell_gaps: bool, logger: logging.Logger
) -> None:
    """Surface unmatched sells at the CLI boundary.

    Without auto-fix this is fatal (exit 2). With it, each *synthesized* residual lot is
    recorded as a default-visible warning, so the synthetic cost basis behind the
    affected realized lines is never silent. Gaps the policy could *not* fix (missing
    Basis, guardrail violations) are already warned about by the matcher as they occur,
    so they are not repeated here.
    """
    if not gaps:
        return

    if not fix_sell_gaps:
        for ge in gaps:
            logger.error(
                "Unmatched SELL: symbol=%s date=%s qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        logger.error(
            "Encountered %d unmatched sell(s). "
            "Rerun with --auto-fix-sell-gaps to synthesize residual lots "
            "from IBKR Basis.",
            len(gaps),
        )
        raise SystemExit(2)

    for ge in gaps:
        if ge.fixed:
            logger.warning(
                "Synthesized residual lot for unmatched SELL: symbol=%s date=%s "
                "qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )


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


def process_files(args: argparse.Namespace) -> None:
    # Get logger for this module
    logger = logging.getLogger(__name__)

    # Parse one or more CSVs
    fix_sell_gaps = getattr(args, "auto_fix_sell_gaps", False)
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

    # Extraction and validation surface predictable input defects as DataQualityError;
    # translate them to a clean exit-2 here rather than letting them escape as a raw
    # traceback (exit 1). Consistent with the parse-error abort above.
    try:
        trades = parse_trades_stocklike(model, asset_scope="stocks_etfs")
        transfers = parse_transfers(model)
        dividends = parse_dividends(model)
        withholding = parse_withholding_tax(model)
        syep_interest = parse_syep_interest_details(model)
        interest = parse_interest(model)

        logger.info(
            "Extracted: %d trades, %d dividends, %d withholding, %d interest, "
            "%d transfers",
            len(trades),
            len(dividends),
            len(withholding),
            len(interest),
            len(transfers),
        )

        validate_symbol_currency_uniqueness(trades, transfers)
    except DataQualityError as e:
        logger.error("%s", e)
        raise SystemExit(2) from e

    # Build FIFO realized
    matcher = FifoMatcher(fix_sell_gaps=fix_sell_gaps)

    # Merge trades and transfers into a single chronological stream so that FIFO lot
    # creation/consumption respects actual event ordering.
    # Same-date tie-break: transfer-in(0) < trades by datetime(1) < transfer-out(2).
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

    _report_sell_gaps(matcher.gap_events, fix_sell_gaps, logger)

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
            logger.exception("Failed to prepare FX conversion: %s", e)
            raise
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
                # Synthesized basis is backed out of IBKR's own `Basis`, so it agrees
                # with IBKR by construction; report it separately so a green
                # reconciliation is not read as independent confirmation.
                logger.info(
                    "Reconciliation: %d symbol(s) carry synthesized basis -- not "
                    "independently confirmed: %s",
                    len(report.synthetic),
                    ", ".join(f"{r.symbol} ({r.currency})" for r in report.synthetic),
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
        "--auto-fix-sell-gaps",
        action="store_true",
        help=(
            "When a SELL lacks sufficient buy lots, use IBKR per-trade Basis "
            "to synthesize a residual lot for the remaining quantity."
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
