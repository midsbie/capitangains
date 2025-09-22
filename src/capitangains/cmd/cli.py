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
from decimal import ROUND_HALF_UP, Decimal, getcontext
from typing import Optional
from pathlib import Path

from capitangains.logging import configure_logging
from capitangains.model import (
    IbkrStatementCsvParser,
    merge_models,
    merge_reports,
)
from capitangains.reporting import (
    FxTable,
    FifoMatcher,
    ReportBuilder,
    parse_dividends,
    parse_syep_interest_details,
    parse_interest,
    parse_trades_stocklike,
    parse_withholding_tax,
    reconcile_with_ibkr_summary,
)
from capitangains.reporting.report_sink import ExcelReportSink

# Monetary precision and rounding
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_UP

logger = configure_logging()


def process_files(args):
    # Parse one or more CSVs
    fix_sell_gaps = getattr(args, "auto_fix_sell_gaps", False)
    inputs = args.input if isinstance(args.input, list) else [args.input]
    logger.info("Reading %d file(s): %s", len(inputs), ", ".join(inputs))

    parser = IbkrStatementCsvParser()
    models = []
    reports = []
    for p in inputs:
        m, rep = parser.parse_file(p)
        models.append(m)
        reports.append(rep)
    model = merge_models(models)
    merge_reports(reports).log_with(logger)

    # Extract data
    trades = parse_trades_stocklike(model, asset_scope="stocks_etfs")
    dividends = parse_dividends(model)
    withholding = parse_withholding_tax(model)
    syep_interest = parse_syep_interest_details(model)
    interest = parse_interest(model)

    # Build FIFO realized
    matcher = FifoMatcher(fix_sell_gaps=fix_sell_gaps)
    realized = []
    for tr in trades:
        rl = matcher.ingest(tr)
        if rl is not None and rl.sell_date.year == args.year:
            realized.append(rl)

    # If auto-fix is disabled and there were unmatched sells, abort without writing outputs
    if not fix_sell_gaps and matcher.gap_events:
        for ge in matcher.gap_events:
            logger.error(
                "Unmatched SELL: symbol=%s date=%s qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        logger.error(
            "Encountered %d unmatched sell(s). Rerun with --auto-fix-sell-gaps to synthesize residual lots from IBKR Basis.",
            len(matcher.gap_events),
        )
        raise SystemExit(2)

    # Build report
    rb = ReportBuilder(year=args.year)
    for rl in realized:
        rb.add_realized(rl)
    rb.set_dividends([d for d in dividends if d["date"].year == args.year])
    rb.set_withholding([w for w in withholding if w["date"].year == args.year])

    # Keep only rows with a value date in the selected year (drop CSV 'Total' lines)
    rb.set_syep_interest(
        [
            r
            for r in syep_interest
            if r.get("value_date") and r["value_date"].year == args.year
        ]
    )
    rb.set_interest([i for i in interest if i["date"].year == args.year])

    # FX conversion if provided
    fx: Optional[FxTable] = None
    if args.fx_table:
        try:
            fx = FxTable.from_csv(args.fx_table)
        except Exception as e:
            logger.exception("Failed to prepare FX conversion: %s", e)
            raise
    rb.convert_eur(fx)

    # Soft reconciliation
    if len(inputs) == 1:
        try:
            ibkr_sum = reconcile_with_ibkr_summary(model)
            if ibkr_sum:
                mismatches = []
                for sym, ibkr_val in ibkr_sum.items():
                    my_val = rb.symbol_totals.get(sym, {}).get("realized_eur", None)
                    if my_val is not None:
                        if (my_val - ibkr_val).copy_abs() > Decimal("0.05"):
                            mismatches.append((sym, my_val, ibkr_val))
                if mismatches:
                    logger.warning(
                        "Reconciliation mismatches (my EUR vs IBKR EUR): %s",
                        mismatches[:5],
                    )
        except Exception:
            logger.exception("Reconciliation failed; continuing without it.")
    else:
        logger.info(
            "Skipping IBKR summary reconciliation for multi-file input (spans multiple periods)."
        )

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(f"report_{args.year}.xlsx")

    # Write outputs via sink
    sink = ExcelReportSink(out_path=out_path, locale=args.locale)
    out_path = sink.write(rb)
    logger.info("Wrote workbook to %s", out_path)


def build_argparser():
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
            "When a SELL lacks sufficient buy lots, use IBKR per-trade Basis to synthesize a residual lot for the remaining quantity."
        ),
    )
    return p


def main():
    parser = build_argparser()
    args = parser.parse_args()
    process_files(args)


if __name__ == "__main__":
    raise SystemExit(main())
