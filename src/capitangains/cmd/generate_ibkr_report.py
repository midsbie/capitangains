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
    python -m capitangains.cmd.generate_ibkr_report \
        --year 2024 \
        --input /path/to/ActivityStatement_2024.csv \
        --output ./out.xlsx \
        --fx-table ./fx_rates.csv

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
from capitangains.model import IbkrStatementCsvParser
from capitangains.reporting import (
    FxTable,
    FifoMatcher,
    ReportBuilder,
    parse_dividends,
    parse_trades_stocklike,
    parse_withholding_tax,
    reconcile_with_ibkr_summary,
)
from capitangains.reporting.report_sink import ExcelReportSink, OdsReportSink

# Monetary precision and rounding
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_UP

logger = configure_logging()


def process_files(args):
    # Parse CSVs
    logger.info("Reading %s", args.input)

    parser = IbkrStatementCsvParser()
    model, report = parser.parse_file(args.input)
    report.log_with(logger)

    # Extract data
    trades = parse_trades_stocklike(model, asset_scope=args.asset_scope)
    dividends = parse_dividends(model)
    withholding = parse_withholding_tax(model)

    # Build FIFO realized
    matcher = FifoMatcher()
    realized = []
    for tr in trades:
        rl = matcher.ingest(tr)
        if rl is not None and rl.sell_date.year == args.year:
            realized.append(rl)

    # Build report
    rb = ReportBuilder(year=args.year)
    for rl in realized:
        rb.add_realized(rl)
    rb.set_dividends([d for d in dividends if d["date"].year == args.year])
    rb.set_withholding([w for w in withholding if w["date"].year == args.year])

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
                    "Reconciliation mismatches (my EUR vs IBKR EUR): %s", mismatches[:5]
                )
    except Exception:
        logger.exception("Reconciliation failed; continuing without it.")

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        ext = "xlsx" if args.format == "xlsx" else "ods"
        out_path = Path(f"report_{args.year}.{ext}")

    # Write outputs via sink
    if args.format == "xlsx":
        sink = ExcelReportSink(out_path=out_path, locale=args.locale)
    elif args.format == "ods":
        sink = OdsReportSink(out_path=out_path)
    else:
        raise ValueError(f"Unknown output format: {args.format}")
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
        "--input",
        type=str,
        required=True,
        help="One or more Activity Statement CSV paths",
    )
    p.add_argument(
        "--asset-scope",
        type=str,
        default="stocks",
        choices=["stocks", "etfs", "stocks_etfs", "all"],
        help="Asset filter scope",
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
        "--format",
        type=str,
        default="xlsx",
        choices=["xlsx", "ods"],
        help="Output workbook format",
    )
    p.add_argument(
        "--locale",
        type=str,
        default="PT",
        choices=["PT", "EN"],
        help="Locale for headers and sheet names (PT or EN)",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output filename (e.g., report.xlsx). If omitted, uses report_<year>.<ext>",
    )
    return p


def main():
    parser = build_argparser()
    args = parser.parse_args()
    process_files(args)


if __name__ == "__main__":
    main()
