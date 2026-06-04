"""CLI-level aggregate diagnostics.

- ``_report_sell_gaps``: fatal without auto-fix; per-synthesized-lot audit warning with
  it (so synthetic cost basis is never silent).
- ``process_files``: aborts (exit 2, no workbook) when the FX table cannot supply
  every rate the EUR report needs.
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


def test_process_files_exits_2_when_fx_table_incomplete(tmp_path, caplog):
    # Non-EUR data with no FX table cannot be converted. A complete table is a
    # precondition, so this aborts with exit 2 and writes no workbook with
    # blank/substituted EUR figures (findings #2/#3), rather than warning + exit 0.
    stmt = tmp_path / "stmt.csv"
    _write_statement(stmt)
    out = tmp_path / "out.xlsx"
    args = argparse.Namespace(
        input=[str(stmt)],
        year=2024,
        fx_table=None,  # no FX table: USD proceeds/cost cannot be converted to EUR
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=False,
        verbose=0,
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Missing FX rate" in r.getMessage() for r in caplog.records)


_TRADES_HEADER = [
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
]


def _trade(symbol, currency, *, date="2024-01-10, 10:00:00", qty="10"):
    return [
        "Trades",
        "Data",
        "Order",
        "Stocks",
        currency,
        symbol,
        date,
        qty,
        "100",
        "-1000",
        "-1",
        "O",
        "1000",
        "0",
    ]


def _args(stmt, out):
    return argparse.Namespace(
        input=[str(stmt)],
        year=2024,
        fx_table=None,
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=False,
        verbose=0,
    )


def test_process_files_exits_2_on_malformed_trade_row(tmp_path, caplog):
    # A row-level parse defect (blank Date/Time) must abort cleanly with exit 2, not a
    # raw traceback (exit 1), and write no workbook.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows([_TRADES_HEADER, _trade("AAPL", "USD", date="")])

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Date/Time" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_symbol_currency_violation(tmp_path, caplog):
    # A legitimate-but-rejected data condition (one symbol in two currencies) must abort
    # with exit 2, not a raw traceback.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(
            [_TRADES_HEADER, _trade("ABC", "USD"), _trade("ABC", "EUR")]
        )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("symbol-currency uniqueness" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_malformed_dividend_amount(tmp_path, caplog):
    # A present-but-malformed cash-flow value (bad dividend Amount) must abort with
    # exit 2 like trades/SYEP/transfers, not escape as a raw traceback. The
    # skip-incomplete gate only drops rows missing core fields, not malformed amounts.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(
            [
                ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
                ["Dividends", "Data", "USD", "2024-01-15", "AAPL Dividend", "invalid"],
            ]
        )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Amount" in r.getMessage() for r in caplog.records)
