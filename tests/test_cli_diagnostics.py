"""Aggregate diagnostics and full-pipeline behavior.

Exercises ``capitangains.diagnostics`` (the boundary report helpers) and
``capitangains.pipeline.run`` (the orchestration) together:

- ``parse_acknowledged_gaps``: parse the itemized ``SYMBOL@YYYY-MM-DD`` acknowledgment
  spec; every malformed token is collected and reported as one ``DataQualityError``.
- ``report_gap_acknowledgments``: two-way tie-out of the gaps found against the
  operator's acknowledgments. Any unacknowledged gap, acknowledged-but-defective Basis,
  or orphan acknowledgment is fatal (exit 2); a clean run emits one audit warning per
  synthesized lot, so synthetic cost basis is never silent.
- ``run``: aborts (exit 2, no workbook) on a malformed spec, a failed gap tie-out, or an
  FX table that cannot supply every rate the EUR report needs; exits 1 on an unreadable
  or unparseable FX table (a setup failure, not a data defect). Under ``--dry-run`` it
  runs the full preflight and stops before the write, leaving any existing output
  untouched.
"""

import csv
import datetime as dt
import logging
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from capitangains.cmd.cli import build_argparser
from capitangains.diagnostics import (
    format_reconciliation_sample,
    parse_acknowledged_gaps,
    report_gap_acknowledgments,
    report_reconciliation,
    report_statement_input_conflicts,
    report_transfer_ordering_collisions,
)
from capitangains.errors import DataQualityError
from capitangains.model import IbkrStatementCsvParser
from capitangains.pipeline import RunOptions, run
from capitangains.reporting import ReconciliationReport, SymbolReconciliation
from capitangains.reporting.extract import TradeRow, TransferRow
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


# --- parse_acknowledged_gaps ---------------------------------------------------------


def test_parse_acknowledged_gaps_none_and_empty_yield_empty_set():
    assert parse_acknowledged_gaps(None) == frozenset()
    assert parse_acknowledged_gaps("") == frozenset()


def test_parse_acknowledged_gaps_skips_empty_tokens():
    # Leading, trailing, and doubled commas (plus surrounding whitespace) are skipped,
    # not errors; the real key survives.
    assert parse_acknowledged_gaps(" ,BABA@2024-01-01,") == frozenset(
        {("BABA", dt.date(2024, 1, 1))}
    )


def test_parse_acknowledged_gaps_dedupes_repeated_keys():
    assert parse_acknowledged_gaps("BABA@2024-01-01,BABA@2024-01-01") == frozenset(
        {("BABA", dt.date(2024, 1, 1))}
    )


def test_parse_acknowledged_gaps_rejects_malformed_tokens():
    # No "@", empty symbol, empty date, and unparseable date are each malformed.
    for bad in ("BABAnoat", "@2024-01-01", "BABA@", "BABA@notadate"):
        with pytest.raises(DataQualityError):
            parse_acknowledged_gaps(bad)


def test_parse_acknowledged_gaps_lists_every_malformed_token():
    # Accumulate, do not fail fast: both bad tokens appear so the spec is fixed in one
    # pass.
    with pytest.raises(DataQualityError) as exc:
        parse_acknowledged_gaps("BABAnoat,VOD@nope")
    msg = str(exc.value)
    assert "BABAnoat" in msg and "VOD@nope" in msg


# --- report_gap_acknowledgments ------------------------------------------------------


def test_report_gap_acknowledgments_no_gaps_no_acks_is_silent(caplog):
    logger = logging.getLogger("gaps_none")
    with caplog.at_level(logging.INFO):
        report_gap_acknowledgments([], frozenset(), logger)
    assert caplog.records == []


def test_report_gap_acknowledgments_unacknowledged_gap_is_fatal(caplog):
    logger = logging.getLogger("gaps_unack")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_gap_acknowledgments(
            [_gap(outcome=GapResolution.UNACKNOWLEDGED)], frozenset(), logger
        )

    assert exc.value.code == 2
    assert any("Unacknowledged SELL gap" in r.getMessage() for r in caplog.records)


def test_report_gap_acknowledgments_synthesized_gap_warns(caplog):
    logger = logging.getLogger("gaps_synth")
    acknowledged = frozenset({("AAPL", dt.date(2024, 1, 1))})
    with caplog.at_level(logging.WARNING):
        report_gap_acknowledgments(
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
        report_gap_acknowledgments([unack, defective], acknowledged, logger)

    assert exc.value.code == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any("Unacknowledged SELL gap" in m and "UNACK" in m for m in messages)
    assert any("defective IBKR Basis" in m and "CORRUPT" in m for m in messages)
    assert any("Orphan acknowledgment" in m and "GHOST" in m for m in messages)
    assert any("tie-out failed" in m for m in messages)


# --- report_transfer_ordering_collisions ---------------------------------------------


def _trade_row(symbol, currency, date, qty="10"):
    quantity = Decimal(qty)
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency=currency,
        symbol=symbol,
        datetime_str=f"{date}, 10:00:00",
        date=dt.date.fromisoformat(date),
        quantity=quantity,
        t_price=Decimal("100"),
        proceeds=Decimal("-1000") if quantity > 0 else Decimal("1000"),
        comm_fee=Decimal("-1"),
        code="O",
    )


def _transfer_row(symbol, currency, date, direction="In"):
    return TransferRow(
        section="Transfers",
        asset_category="Stocks",
        currency=currency,
        symbol=symbol,
        date=dt.date.fromisoformat(date),
        direction=direction,
        quantity=Decimal("10"),
        market_value=Decimal("1000"),
        code="",
    )


def test_report_transfer_ordering_collisions_silent_without_collision(caplog):
    # A transfer on a different day from the trade (same symbol), and a transfer sharing
    # the trade's day but in a different symbol, are both independent for FIFO: no halt,
    # no output. The guard is scoped to (symbol, currency, date).
    logger = logging.getLogger("xfer_none")
    trades = [_trade_row("AAPL", "USD", "2024-06-10")]
    transfers = [
        _transfer_row("AAPL", "USD", "2024-03-15"),
        _transfer_row("MSFT", "USD", "2024-06-10"),
    ]
    with caplog.at_level(logging.ERROR):
        report_transfer_ordering_collisions(trades, transfers, logger)
    assert caplog.records == []


def test_report_transfer_ordering_collisions_two_same_day_transfers_is_fatal(caplog):
    # Two transfers of the same symbol on the same day are equally unorderable -- IBKR
    # timestamps neither -- even with no trade that day.
    logger = logging.getLogger("xfer_pair")
    transfers = [
        _transfer_row("AAPL", "USD", "2024-06-10", "In"),
        _transfer_row("AAPL", "USD", "2024-06-10", "Out"),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_transfer_ordering_collisions([], transfers, logger)

    assert exc.value.code == 2
    assert any(
        "Unorderable same-day events" in r.getMessage() and "AAPL" in r.getMessage()
        for r in caplog.records
    )


def test_report_transfer_ordering_collisions_lists_every_collision(caplog):
    # Accumulate, do not fail fast: two distinct symbol-days each collide, and both are
    # named before the single exit, so every one is visible in one pass.
    logger = logging.getLogger("xfer_accum")
    trades = [
        _trade_row("AAPL", "USD", "2024-06-10"),
        _trade_row("VOD", "GBP", "2024-07-01"),
    ]
    transfers = [
        _transfer_row("AAPL", "USD", "2024-06-10"),
        _transfer_row("VOD", "GBP", "2024-07-01"),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_transfer_ordering_collisions(trades, transfers, logger)

    assert exc.value.code == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any("AAPL" in m for m in messages)
    assert any("VOD" in m for m in messages)
    assert any("2 symbol-day(s)" in m for m in messages)


# --- report_statement_input_conflicts ------------------------------------------------


def _meta_model(*, account=None, period=None):
    """Build an IbkrModel carrying just the Account/Statement metadata under test."""
    rows = []
    if period is not None:
        rows += [
            ["Statement", "Header", "Field Name", "Field Value"],
            ["Statement", "Data", "Period", period],
        ]
    if account is not None:
        rows += [
            ["Account Information", "Header", "Field Name", "Field Value"],
            ["Account Information", "Data", "Account", account],
        ]
    model, _ = IbkrStatementCsvParser().parse_rows(rows)
    return model


_Y2023 = "January 1, 2023 - December 31, 2023"
_Y2024 = "January 1, 2024 - December 31, 2024"


def test_statement_conflicts_single_file_is_skipped():
    # A lone statement has nothing to overlap; the check never inspects its metadata.
    logger = logging.getLogger("conflicts_single")
    report_statement_input_conflicts(
        ["a.csv"], [_meta_model(account="U1", period=_Y2024)], logger
    )


def test_statement_conflicts_disjoint_periods_ok():
    logger = logging.getLogger("conflicts_disjoint")
    models = [
        _meta_model(account="U1", period=_Y2023),
        _meta_model(account="U1", period=_Y2024),
    ]
    report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)


def test_statement_conflicts_overlapping_periods_fatal(caplog):
    logger = logging.getLogger("conflicts_overlap")
    models = [
        _meta_model(account="U1", period=_Y2024),
        _meta_model(account="U1", period="June 1, 2024 - December 31, 2024"),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)

    assert exc.value.code == 2
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "overlapping periods" in m and "a.csv" in m and "b.csv" in m for m in messages
    )


def test_statement_conflicts_same_period_twice_fatal(caplog):
    # The same file passed twice is the degenerate identical-interval overlap.
    logger = logging.getLogger("conflicts_dup")
    model = _meta_model(account="U1", period=_Y2024)
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "a.csv"], [model, model], logger)

    assert exc.value.code == 2
    assert any("overlapping periods" in r.getMessage() for r in caplog.records)


def test_statement_conflicts_multiple_accounts_fatal(caplog):
    # Different accounts, even with disjoint periods, are out of scope (co-mingling).
    logger = logging.getLogger("conflicts_accounts")
    models = [
        _meta_model(account="U1", period=_Y2023),
        _meta_model(account="U2", period=_Y2024),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)

    assert exc.value.code == 2
    assert any(
        "multiple accounts" in r.getMessage()
        and "U1" in r.getMessage()
        and "U2" in r.getMessage()
        for r in caplog.records
    )


def test_statement_conflicts_missing_period_fatal(caplog):
    logger = logging.getLogger("conflicts_no_period")
    models = [
        _meta_model(account="U1", period=_Y2023),
        _meta_model(account="U1", period=None),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)

    assert exc.value.code == 2
    assert any("missing reporting period" in r.getMessage() for r in caplog.records)


def test_statement_conflicts_missing_account_fatal(caplog):
    logger = logging.getLogger("conflicts_no_account")
    models = [
        _meta_model(account="U1", period=_Y2023),
        _meta_model(account=None, period=_Y2024),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)

    assert exc.value.code == 2
    assert any("missing account number" in r.getMessage() for r in caplog.records)


def test_statement_conflicts_unparseable_period_fatal(caplog):
    logger = logging.getLogger("conflicts_bad_period")
    models = [
        _meta_model(account="U1", period=_Y2023),
        _meta_model(account="U1", period="2024-01-01 to 2024-12-31"),
    ]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv"], models, logger)

    assert exc.value.code == 2
    assert any("statement period" in r.getMessage().lower() for r in caplog.records)


def test_statement_conflicts_accumulates_every_problem(caplog):
    # Three statements, three overlapping pairs: all are listed before the single exit.
    logger = logging.getLogger("conflicts_accum")
    models = [_meta_model(account="U1", period=_Y2024) for _ in range(3)]
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        report_statement_input_conflicts(["a.csv", "b.csv", "c.csv"], models, logger)

    assert exc.value.code == 2
    overlaps = [r for r in caplog.records if "overlapping periods" in r.getMessage()]
    assert len(overlaps) == 3  # (a,b), (a,c), (b,c)


# --- run() integration --------------------------------------------------------


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

_TRANSFERS_HEADER = [
    "Transfers",
    "Header",
    "Asset Category",
    "Currency",
    "Symbol",
    "Date",
    "Direction",
    "Qty",
    "Market Value",
    "Code",
]


def _transfer_data(symbol, currency, date, *, direction="In", qty="10"):
    return [
        "Transfers",
        "Data",
        "Stocks",
        currency,
        symbol,
        date,
        direction,
        qty,
        "1000",
        "",
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


def _write_statement(path, *, currency="USD"):
    # A matched BUY(10) then SELL(10): no gap, so the gap tie-out is a no-op. USD needs
    # an FX table to convert; EUR converts by identity and runs clean to the write step.
    rows = [
        _TRADES_HEADER,
        _trade("AAPL", currency),
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            currency,
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


def _args(stmt, out, *, fx_table=None, auto_fix_sell_gaps=None, dry_run=False):
    return RunOptions(
        inputs=[str(stmt)],
        year=2024,
        fx_table=fx_table,
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=auto_fix_sell_gaps,
        dry_run=dry_run,
    )


def test_process_files_exits_2_when_fx_table_incomplete(tmp_path, caplog):
    # Non-EUR data with no FX table cannot be converted. A complete table is a
    # precondition, so this aborts with exit 2 and writes no workbook with
    # blank/substituted EUR figures, rather than warning + exit 0.
    stmt = tmp_path / "stmt.csv"
    _write_statement(stmt)
    out = tmp_path / "out.xlsx"

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Missing FX rate" in r.getMessage() for r in caplog.records)


def test_process_files_exits_1_when_fx_table_unreadable(tmp_path, caplog):
    # A missing or unparseable --fx-table file is a setup failure, not a statement-data
    # defect, so it halts with exit 1 -- a class apart from the curated gates' exit 2 --
    # surfaced as one clean ERROR (explicit, not an emergent raw crash) and no workbook.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt)  # USD: needs the FX table to convert
    args = _args(stmt, out, fx_table=str(tmp_path / "does_not_exist.csv"))

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

    assert exc.value.code == 1
    assert not out.exists()
    assert any(
        "Failed to prepare FX conversion" in r.getMessage() for r in caplog.records
    )


def test_process_files_exits_2_on_malformed_acknowledgment_spec(tmp_path, caplog):
    # A malformed spec must abort before any file is read (fail fast), with exit 2, no
    # workbook, and an ERROR naming the bad token.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt)  # valid statement; only the spec is malformed
    args = _args(stmt, out, auto_fix_sell_gaps="BABA@oops")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

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
        run(_args(stmt, out))

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
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("symbol-currency uniqueness" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_same_day_transfer_trade_collision(tmp_path, caplog):
    # IBKR gives transfers no intraday time, so a transfer landing on the same day as a
    # trade in the SAME symbol cannot be ordered for FIFO. The tool refuses to guess and
    # aborts with exit 2 -- naming the symbol/day -- rather than fabricate an order.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    rows = [
        _TRADES_HEADER,
        _trade("AAPL", "USD", date="2024-06-10, 10:00:00"),
        _TRANSFERS_HEADER,
        _transfer_data("AAPL", "USD", "2024-06-10"),
    ]
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any(
        "Unorderable same-day events" in r.getMessage() and "AAPL" in r.getMessage()
        for r in caplog.records
    )


def test_process_files_allows_same_day_transfer_in_a_different_symbol(tmp_path, caplog):
    # The guard is scoped to (symbol, currency): a transfer sharing a day with trades in
    # an UNRELATED symbol is independent for FIFO and must not halt. EUR converts by
    # identity, so the run completes and writes the workbook.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    rows = [
        _TRADES_HEADER,
        _trade("AAPL", "EUR", date="2024-01-10, 10:00:00"),
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "EUR",
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
        _TRANSFERS_HEADER,
        _transfer_data("MSFT", "EUR", "2024-06-10"),
    ]
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)

    with caplog.at_level(logging.ERROR):
        run(_args(stmt, out))

    assert out.exists()
    assert not any(
        "Unorderable same-day events" in r.getMessage() for r in caplog.records
    )


def test_process_files_transfers_sheet_shows_only_reporting_year(tmp_path):
    # The report is a single-year document, so the Stock Transfers sheet -- like every
    # other category -- shows only args.year. A prior-year transfer is supplied solely
    # to seed FIFO (here SEEDLOT, 2023); it must not surface on the 2024 sheet as if it
    # were a current-year event, while a genuine 2024 transfer (CURRENTXFER) must. Both
    # are IN transfers seeding open lots; all EUR, so the run converts by identity.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    rows = [
        _TRANSFERS_HEADER,
        _transfer_data("SEEDLOT", "EUR", "2023-11-01"),
        _transfer_data("CURRENTXFER", "EUR", "2024-03-01"),
    ]
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)

    run(_args(stmt, out))

    assert out.exists()
    ws = load_workbook(out)["Stock Transfers"]
    symbols = {row[1] for row in ws.iter_rows(min_row=2, values_only=True)}
    assert symbols == {"CURRENTXFER"}


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
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Amount" in r.getMessage() for r in caplog.records)


def test_process_files_lists_every_extraction_defect(tmp_path, caplog):
    # Accumulate, do not fail fast: a malformed trade row AND a malformed dividend row
    # in the same statement are BOTH reported in one run, then a single exit 2 with no
    # workbook -- so the operator fixes every defect in one pass, not one-per-rerun.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    rows = [
        _TRADES_HEADER,
        _trade("AAPL", "USD", date=""),  # bad trade: blank Date/Time
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "USD", "2024-01-15", "AAPL Dividend", "invalid"],
    ]
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    messages = [r.getMessage() for r in caplog.records]
    assert any("Trades" in m and "Date/Time" in m for m in messages)
    assert any("Dividends" in m and "Amount" in m for m in messages)
    assert any("rejected 2 row(s)" in m for m in messages)


def test_process_files_exits_2_on_unacknowledged_gap(tmp_path, caplog):
    # A real SELL gap left unacknowledged (no spec) is fatal: a known taxable disposal
    # must never be silently valued at zero cost.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_single_sell(
        stmt, symbol="ORPH", currency="USD", basis="-1000", realized="200"
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(_args(stmt, out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Unacknowledged SELL gap" in r.getMessage() for r in caplog.records)


def test_process_files_exits_2_on_orphan_acknowledgment(tmp_path, caplog):
    # A clean statement (no gaps) with an acknowledgment that matches nothing is fatal:
    # the operator must not carry a stale or mistyped acknowledgment.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt)  # fully matched: no gaps
    args = _args(stmt, out, auto_fix_sell_gaps="GHOST@2024-01-01")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

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
    args = _args(stmt, out, auto_fix_sell_gaps="CORRUPT@2024-06-10")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

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
    args = _args(stmt, out, auto_fix_sell_gaps="NOBASIS@2024-06-10")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

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
    args = _args(stmt, out, auto_fix_sell_gaps="EURGAP@2024-06-10")

    with caplog.at_level(logging.WARNING):
        run(args)

    assert out.exists()
    assert any("Synthesized residual lot" in r.getMessage() for r in caplog.records)


# --- --dry-run / -n -------------------------------------------------------------------


def test_argparser_accepts_dry_run_short_and_long_flags():
    parser = build_argparser()
    for flag in ("-n", "--dry-run"):
        args = parser.parse_args(["--year", "2024", "stmt.csv", flag])
        assert args.dry_run is True
    # Absent by default: a normal run still writes.
    assert parser.parse_args(["--year", "2024", "stmt.csv"]).dry_run is False


def test_process_files_dry_run_writes_no_workbook_on_clean_input(tmp_path, caplog):
    # A run that would otherwise succeed writes nothing under --dry-run: it returns
    # normally (exit 0), leaves no file at out_path, and announces the preflight.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt, currency="EUR")  # matched, EUR: clean through to the write
    args = _args(stmt, out, dry_run=True)

    with caplog.at_level(logging.INFO):
        run(args)  # no SystemExit

    assert not out.exists()
    assert any("Dry run" in r.getMessage() for r in caplog.records)


def test_process_files_dry_run_does_not_overwrite_existing_output(tmp_path):
    # The existing report at out_path is left byte-for-byte untouched: --dry-run never
    # clobbers a prior good workbook.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    _write_statement(stmt, currency="EUR")
    out.write_bytes(b"prior report")
    args = _args(stmt, out, dry_run=True)

    run(args)

    assert out.read_bytes() == b"prior report"


def test_process_files_dry_run_still_exits_2_on_defect(tmp_path, caplog):
    # --dry-run validates; it does not suppress failures. A row-level defect still
    # aborts with exit 2 and writes no workbook, exactly as a real run would.
    stmt = tmp_path / "stmt.csv"
    out = tmp_path / "out.xlsx"
    with open(stmt, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows([_TRADES_HEADER, _trade("AAPL", "EUR", date="")])
    args = _args(stmt, out, dry_run=True)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(args)

    assert exc.value.code == 2
    assert not out.exists()
    assert any("Date/Time" in r.getMessage() for r in caplog.records)


# --- run() multi-file overlap gate --------------------------------------------


def _statement_meta_rows(account, period):
    return [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "Title", "Activity Statement"],
        ["Statement", "Data", "Period", period],
        ["Account Information", "Header", "Field Name", "Field Value"],
        ["Account Information", "Data", "Account", account],
    ]


def _write_full_statement(path, *, account, period, year, currency="EUR"):
    # A matched BUY(10)/SELL(10) in `year` plus the Account/Statement metadata the
    # multi-file overlap gate reads. EUR converts by identity, so no FX table is needed.
    buy = _trade("AAPL", currency, date=f"{year}-02-10, 10:00:00")
    sell = [
        "Trades",
        "Data",
        "Order",
        "Stocks",
        currency,
        "AAPL",
        f"{year}-06-10, 10:00:00",
        "-10",
        "110",
        "1100",
        "-1",
        "C",
        "-1001",
        "99",
    ]
    rows = _statement_meta_rows(account, period) + [_TRADES_HEADER, buy, sell]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)


def _args_multi(stmts, out, *, year=2024):
    return RunOptions(
        inputs=[str(s) for s in stmts],
        year=year,
        fx_table=None,
        locale="EN",
        output=str(out),
        auto_fix_sell_gaps=None,
        dry_run=False,
    )


def test_process_files_disjoint_statements_write_workbook(tmp_path):
    # Two single-account statements for adjacent, non-overlapping years merge cleanly
    # and produce a workbook: the gate must not flag a legitimate multi-file run.
    a = tmp_path / "2023.csv"
    b = tmp_path / "2024.csv"
    out = tmp_path / "out.xlsx"
    _write_full_statement(a, account="U1", period=_Y2023, year=2023)
    _write_full_statement(b, account="U1", period=_Y2024, year=2024)

    run(_args_multi([a, b], out))

    assert out.exists()


def test_process_files_exits_2_on_overlapping_statements(tmp_path, caplog):
    # The same period supplied twice would double-count trades into FIFO; the run aborts
    # before merging and writes no workbook.
    a = tmp_path / "2024_a.csv"
    b = tmp_path / "2024_b.csv"
    out = tmp_path / "out.xlsx"
    _write_full_statement(a, account="U1", period=_Y2024, year=2024)
    _write_full_statement(b, account="U1", period=_Y2024, year=2024)

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        run(_args_multi([a, b], out))

    assert exc.value.code == 2
    assert not out.exists()
    assert any("overlapping periods" in r.getMessage() for r in caplog.records)


# --- format_reconciliation_sample ----------------------------------------------------


def _recon(symbol, computed, ibkr):
    return SymbolReconciliation(
        symbol=symbol, currency="USD", computed=computed, ibkr=ibkr, n_sells=1
    )


def test_format_reconciliation_sample_lists_all_when_within_cap():
    items = [_recon("AAA", Decimal("1"), Decimal("2"))]
    rendered = format_reconciliation_sample(items)
    assert "showing" not in rendered
    assert "AAA (USD) mine=1 IBKR=2 diff=1" in rendered


def test_format_reconciliation_sample_labels_truncation():
    # More than the cap: the line announces "showing K of N" so a truncated sample is
    # never mistaken for the full set, and emits exactly the cap's worth of entries.
    items = [_recon(f"S{i:02d}", Decimal("1"), Decimal("3")) for i in range(12)]
    rendered = format_reconciliation_sample(items)
    assert rendered.startswith("showing 10 of 12:")
    assert rendered.count("mine=") == 10
    assert "S00" in rendered and "S09" in rendered  # first 10 shown
    assert "S10" not in rendered and "S11" not in rendered  # remainder truncated


def test_report_reconciliation_separates_classes_by_severity(caplog):
    # Sign flips and magnitude gaps each get their own WARNING; synthesized-basis keys
    # are reported apart at INFO. One render, three distinct, correctly-leveled lines.
    report = ReconciliationReport(
        reconciled=[
            _recon("FLIP", Decimal("1000"), Decimal("-2000")),  # sign flip
            _recon("GAP", Decimal("1000"), Decimal("5000")),  # magnitude gap
        ],
        synthetic=[_recon("SYN", Decimal("42"), Decimal("42"))],
        incomplete=[],
    )
    logger = logging.getLogger("recon_render")

    with caplog.at_level(logging.DEBUG, logger="recon_render"):
        report_reconciliation(report, logger)

    leveled = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(
        lvl == logging.WARNING and "gain/loss direction" in m and "FLIP" in m
        for lvl, m in leveled
    )
    assert any(
        lvl == logging.WARNING and "beyond rounding" in m and "GAP" in m
        for lvl, m in leveled
    )
    assert any(
        lvl == logging.INFO and "synthesized basis" in m and "SYN" in m
        for lvl, m in leveled
    )
