"""CLI-level aggregate diagnostics.

- ``_parse_acknowledged_gaps``: parse the itemized ``SYMBOL@YYYY-MM-DD`` acknowledgment
  spec; every malformed token is collected and reported as one ``DataQualityError``.
- ``_report_gap_acknowledgments``: two-way tie-out of the gaps found against the
  operator's acknowledgments. Any unacknowledged gap, acknowledged-but-defective Basis,
  or orphan acknowledgment is fatal (exit 2); a clean run emits one audit warning per
  synthesized lot, so synthetic cost basis is never silent.
- ``process_files``: aborts (exit 2, no workbook) on a malformed spec, a failed gap
  tie-out, or an FX table that cannot supply every rate the EUR report needs.
"""

import argparse
import csv
import datetime as dt
import logging
from decimal import Decimal

import pytest

from capitangains.cmd.cli import (
    _parse_acknowledged_gaps,
    _report_gap_acknowledgments,
    process_files,
)
from capitangains.errors import DataQualityError
from capitangains.reporting.fifo_domain import GapEvent, GapResolution


def _gap(*, outcome, symbol="AAPL", date=dt.date(2024, 1, 1), message="no buy history"):
    return GapEvent(
        symbol=symbol,
        date=date,
        remaining_qty=Decimal("5"),
        currency="USD",
        message=message,
        outcome=outcome,
    )


# --- _parse_acknowledged_gaps ---------------------------------------------------------


def test_parse_acknowledged_gaps_none_and_empty_yield_empty_set():
    assert _parse_acknowledged_gaps(None) == frozenset()
    assert _parse_acknowledged_gaps("") == frozenset()


def test_parse_acknowledged_gaps_skips_empty_tokens():
    # Leading, trailing, and doubled commas (plus surrounding whitespace) are skipped,
    # not errors; the real key survives.
    assert _parse_acknowledged_gaps(" ,BABA@2024-01-01,") == frozenset(
        {("BABA", dt.date(2024, 1, 1))}
    )


def test_parse_acknowledged_gaps_dedupes_repeated_keys():
    assert _parse_acknowledged_gaps("BABA@2024-01-01,BABA@2024-01-01") == frozenset(
        {("BABA", dt.date(2024, 1, 1))}
    )


def test_parse_acknowledged_gaps_rejects_malformed_tokens():
    # No "@", empty symbol, empty date, and unparseable date are each malformed.
    for bad in ("BABAnoat", "@2024-01-01", "BABA@", "BABA@notadate"):
        with pytest.raises(DataQualityError):
            _parse_acknowledged_gaps(bad)


def test_parse_acknowledged_gaps_lists_every_malformed_token():
    # Accumulate, do not fail fast: both bad tokens appear so the spec is fixed in one
    # pass.
    with pytest.raises(DataQualityError) as exc:
        _parse_acknowledged_gaps("BABAnoat,VOD@nope")
    msg = str(exc.value)
    assert "BABAnoat" in msg and "VOD@nope" in msg


# --- _report_gap_acknowledgments ------------------------------------------------------


def test_report_gap_acknowledgments_no_gaps_no_acks_is_silent(caplog):
    logger = logging.getLogger("gaps_none")
    with caplog.at_level(logging.INFO):
        _report_gap_acknowledgments([], frozenset(), logger)
    assert caplog.records == []


def test_report_gap_acknowledgments_unacknowledged_gap_is_fatal(caplog):
    logger = logging.getLogger("gaps_unack")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        _report_gap_acknowledgments(
            [_gap(outcome=GapResolution.UNACKNOWLEDGED)], frozenset(), logger
        )

    assert exc.value.code == 2
    assert any("Unacknowledged SELL gap" in r.getMessage() for r in caplog.records)


def test_report_gap_acknowledgments_synthesized_gap_warns(caplog):
    logger = logging.getLogger("gaps_synth")
    acknowledged = frozenset({("AAPL", dt.date(2024, 1, 1))})
    with caplog.at_level(logging.WARNING):
        _report_gap_acknowledgments(
            [_gap(outcome=GapResolution.SYNTHESIZED)], acknowledged, logger
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # only the synthesized lot gets an audit-trail warning
    assert "Synthesized residual lot" in warnings[0].getMessage()


def test_report_gap_acknowledgments_accumulates_every_fatal_category(caplog):
    # One run carrying all three fatal conditions must log every one, then a summary,
    # and exit exactly once. No fail-fast that hides later problems from the operator.
    logger = logging.getLogger("gaps_accum")
    unack = _gap(
        outcome=GapResolution.UNACKNOWLEDGED, symbol="UNACK", date=dt.date(2024, 2, 1)
    )
    defective = _gap(
        outcome=GapResolution.DEFECTIVE,
        symbol="CORRUPT",
        date=dt.date(2024, 3, 1),
        message="IBKR Basis (-9999) inconsistent with its Realized P/L",
    )
    acknowledged = frozenset(
        {
            ("CORRUPT", dt.date(2024, 3, 1)),  # matches the defective gap
            ("GHOST", dt.date(2024, 1, 1)),  # orphan: matches no gap this run
        }
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        _report_gap_acknowledgments([unack, defective], acknowledged, logger)

    assert exc.value.code == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any("Unacknowledged SELL gap" in m and "UNACK" in m for m in messages)
    assert any("defective IBKR Basis" in m and "CORRUPT" in m for m in messages)
    assert any("Orphan acknowledgment" in m and "GHOST" in m for m in messages)
    assert any("tie-out failed" in m for m in messages)


# --- process_files integration --------------------------------------------------------


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


def _write_statement(path):
    # A matched BUY(10) then SELL(10): no gap, so the gap tie-out is a no-op.
    rows = [
        _TRADES_HEADER,
        _trade("AAPL", "USD"),
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


def _write_single_sell(path, *, symbol, currency, basis, realized, proceeds="1200"):
    # A lone SELL with no prior BUY: the whole quantity is an unmatched gap.
    sell = [
        "Trades",
        "Data",
        "Order",
        "Stocks",
        currency,
        symbol,
        "2024-06-10, 10:00:00",
        "-10",
        "120",
        proceeds,
        "0",
        "C",
        basis,
        realized,
    ]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows([_TRADES_HEADER, sell])


def _args(stmt, out):
    return argparse.Namespace(
        input=[str(stmt)],
        year=2024,
        fx_table=None,
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=None,
        verbose=0,
    )


def test_process_files_exits_2_when_fx_table_incomplete(tmp_path, caplog):
    # Non-EUR data with no FX table cannot be converted. A complete table is a
    # precondition, so this aborts with exit 2 and writes no workbook with
    # blank/substituted EUR figures, rather than warning + exit 0.
    stmt = tmp_path / "stmt.csv"
    _write_statement(stmt)
    out = tmp_path / "out.xlsx"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Missing FX rate" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_malformed_acknowledgment_spec(tmp_path, caplog):
    # A malformed spec must abort before any file is read (fail fast), with exit 2, no
    # workbook, and an ERROR naming the bad token.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt)  # valid statement; only the spec is malformed
    args = _args(stmt, out)
    args.auto_fix_sell_gaps = "BABA@oops"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any("BABA@oops" in r.getMessage() for r in caplog.records)


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


def test_process_files_exits_2_on_unacknowledged_gap(tmp_path, caplog):
    # A real SELL gap left unacknowledged (no spec) is fatal: a known taxable disposal
    # must never be silently valued at zero cost.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_single_sell(
        stmt, symbol="ORPH", currency="USD", basis="-1000", realized="200"
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Unacknowledged SELL gap" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_orphan_acknowledgment(tmp_path, caplog):
    # A clean statement (no gaps) with an acknowledgment that matches nothing is fatal:
    # the operator must not carry a stale or mistyped acknowledgment.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt)  # fully matched: no gaps
    args = _args(stmt, out)
    args.auto_fix_sell_gaps = "GHOST@2024-01-01"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any(
        "Orphan acknowledgment" in r.getMessage() and "GHOST" in r.getMessage()
        for r in caplog.records
    )


def test_process_files_exits_2_on_acknowledged_gap_with_corrupt_basis(tmp_path, caplog):
    # An acknowledged gap whose Basis contradicts IBKR's own Realized P/L
    # (Proceeds + Comm + Basis != Realized) is DEFECTIVE; synthesizing from it would
    # fabricate the gain, so the run aborts with exit 2 and writes no workbook. The rich
    # message survives the move to the boundary (mentions both "Basis" and "Realized").
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_single_sell(
        stmt, symbol="CORRUPT", currency="USD", basis="-99999", realized="200"
    )
    args = _args(stmt, out)
    args.auto_fix_sell_gaps = "CORRUPT@2024-06-10"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any(
        "Basis" in r.getMessage() and "Realized" in r.getMessage()
        for r in caplog.records
    )


def test_process_files_exits_2_on_acknowledged_gap_with_missing_basis(tmp_path, caplog):
    # An acknowledged gap with no IBKR Basis at all is DEFECTIVE: there is no figure to
    # synthesize from, so the run aborts (exit 2, no workbook) rather than electing a
    # zero basis.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_single_sell(stmt, symbol="NOBASIS", currency="USD", basis="", realized="")
    args = _args(stmt, out)
    args.auto_fix_sell_gaps = "NOBASIS@2024-06-10"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        process_files(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any("defective IBKR Basis" in r.getMessage() for r in caplog.records)


def test_process_files_synthesizes_acknowledged_gap_and_writes_workbook(
    tmp_path, caplog
):
    # An acknowledged gap with a valid IBKR Basis is synthesized: the run completes,
    # writes the workbook, and emits the per-lot audit warning so the synthetic basis is
    # never silent. EUR trades need no FX table (identity conversion).
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_single_sell(
        stmt, symbol="EURGAP", currency="EUR", basis="-1000", realized="200"
    )
    args = _args(stmt, out)
    args.auto_fix_sell_gaps = "EURGAP@2024-06-10"

    with caplog.at_level(logging.WARNING):
        process_files(args)

    assert out.exists()
    assert any("Synthesized residual lot" in r.getMessage() for r in caplog.records)
