"""CLI-level aggregate diagnostics.

- ``_report_sell_gaps``: fatal without auto-fix; per-synthesized-lot audit warning with
  it (so synthetic cost basis is never silent).
- ``process_files``: a default-visible warning when EUR conversion is incomplete.
"""

import argparse
import csv
import datetime as dt
import logging
from decimal import Decimal

import pytest

from capitangains.cmd.cli import _report_sell_gaps, process_files
from capitangains.reporting.fifo_domain import GapEvent


def _gap(*, fixed):
    return GapEvent(
        symbol="AAPL",
        date=dt.date(2024, 1, 1),
        remaining_qty=Decimal("5"),
        currency="USD",
        message="no buy history",
        fixed=fixed,
    )


def test_report_sell_gaps_no_gaps_is_silent(caplog):
    logger = logging.getLogger("gaps_none")
    with caplog.at_level(logging.INFO):
        _report_sell_gaps([], fix_sell_gaps=True, logger=logger)
    assert caplog.records == []


def test_report_sell_gaps_without_autofix_errors_and_exits(caplog):
    logger = logging.getLogger("gaps_off")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        _report_sell_gaps([_gap(fixed=False)], fix_sell_gaps=False, logger=logger)

    assert exc.value.code == 2
    assert any("Unmatched SELL" in r.getMessage() for r in caplog.records)


def test_report_sell_gaps_with_autofix_warns_per_synthesized_lot(caplog):
    logger = logging.getLogger("gaps_on")
    with caplog.at_level(logging.WARNING):
        # One synthesized (fixed) lot and one that could not be fixed.
        _report_sell_gaps(
            [_gap(fixed=True), _gap(fixed=False)], fix_sell_gaps=True, logger=logger
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # only the synthesized lot gets an audit-trail warning
    assert "Synthesized residual lot" in warnings[0].getMessage()


def _write_statement(path):
    rows = [
        [
            "Trades",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Quantity",
            "T. Price",
            "Proceeds",
            "Comm/Fee",
            "Code",
            "Basis",
            "Realized P/L",
        ],
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-10, 10:00:00",
            "10",
            "100",
            "-1000",
            "-1",
            "O",
            "1001",
            "0",
        ],
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "USD",
            "AAPL",
            "2024-06-10, 10:00:00",
            "-10",
            "110",
            "1100",
            "-1",
            "C",
            "-1001",
            "99",
        ],
    ]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)


def test_process_files_warns_when_eur_conversion_incomplete(tmp_path, caplog):
    stmt = tmp_path / "stmt.csv"
    _write_statement(stmt)
    out = tmp_path / "out.xlsx"
    args = argparse.Namespace(
        input=[str(stmt)],
        year=2024,
        fx_table=None,  # no FX table: USD proceeds cannot be converted to EUR
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=False,
        verbose=0,
    )

    with caplog.at_level(logging.WARNING):
        process_files(args)

    assert any("EUR conversion incomplete" in r.getMessage() for r in caplog.records)
    assert out.exists()  # the workbook is still written despite the gap
