"""Diagnostics emitted by extract.py: aggregate skip/elision counts and the
material warning when a whole trades subtable is dropped.

All of these were previously silent (or debug-only); each now surfaces so a run never
drops data without saying so.
"""

import logging

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import (
    parse_dividends,
    parse_interest,
    parse_trades_stocklike,
    parse_transfers,
    parse_withholding_tax,
)

_EXTRACT_LOGGER = "capitangains.reporting.extract"

_TRADE_HEADER = [
    "Trades",
    "Header",
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


def _model(rows):
    model, _ = IbkrStatementCsvParser().parse_rows(rows)
    return model


def _trade_data(asset, symbol, *, basis="1000", realized="0"):
    return [
        "Trades",
        "Data",
        asset,
        "USD",
        symbol,
        "2024-01-10, 10:00:00",
        "10",
        "100",
        "-1000",
        "-1",
        "O",
        basis,
        realized,
    ]


def test_trades_out_of_scope_rows_counted_as_info(caplog):
    model = _model(
        [_TRADE_HEADER, _trade_data("Stocks", "AAPL"), _trade_data("Bonds", "BND")]
    )
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        trades, _ = parse_trades_stocklike(model, asset_scope="stocks_etfs")

    assert [t.symbol for t in trades] == ["AAPL"]  # bond filtered out
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Trades (scope='stocks_etfs'): skipped 1 row(s)" in m for m in msgs)


def test_trades_elided_basis_counted_as_info(caplog):
    model = _model([_TRADE_HEADER, _trade_data("Stocks", "AAPL", basis="...")])
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        parse_trades_stocklike(model, asset_scope="stocks_etfs")

    assert any(
        "1 row(s) with elided Basis, 0 with elided Realized P/L" in r.getMessage()
        for r in caplog.records
    )


def test_trades_subtable_missing_required_column_warns(caplog):
    # Header without "Proceeds": the whole subtable is dropped -- that is material.
    rows = [
        [
            "Trades",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Quantity",
            "T. Price",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-10, 10:00:00",
            "10",
            "100",
            "-1",
            "O",
        ],
    ]
    model = _model(rows)
    with caplog.at_level(logging.WARNING, logger=_EXTRACT_LOGGER):
        trades, _ = parse_trades_stocklike(model, asset_scope="stocks_etfs")

    assert trades == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "missing required column(s) ['Proceeds']" in warnings[0].getMessage()


def test_dividends_incomplete_rows_counted_as_info(caplog):
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "EUR", "2024-01-06", "", "5.00"],  # missing description
    ]
    model = _model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, _ = parse_dividends(model)

    assert len(out) == 1
    assert any(
        "Dividends: skipped 1 incomplete row(s)" in r.getMessage()
        for r in caplog.records
    )


def test_withholding_incomplete_rows_counted_as_info(caplog):
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
            "Code",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-05",
            "ABC Cash Dividend - US Tax",
            "-1.50",
            "",
        ],
        ["Withholding Tax", "Data", "USD", "", "Orphan", "-1.00", ""],  # missing date
    ]
    model = _model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, _ = parse_withholding_tax(model)

    assert len(out) == 1
    assert any(
        "Withholding tax: skipped 1 incomplete row(s)" in r.getMessage()
        for r in caplog.records
    )


def test_interest_incomplete_rows_counted_as_info(caplog):
    rows = [
        ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
        ["Interest", "Data", "USD", "2024-01-05", "Credit Interest", "2.00"],
        ["Interest", "Data", "USD", "2024-01-06", "", "1.00"],  # incomplete
        [
            "Interest",
            "Data",
            "Total",
            "",
            "",
            "10.00",
        ],  # recognized total: stays silent
    ]
    model = _model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, _ = parse_interest(model)

    assert len(out) == 1
    assert any(
        "Interest: skipped 1 incomplete row(s)" in r.getMessage()
        for r in caplog.records
    )


def test_transfers_non_stock_rows_counted_as_info(caplog):
    rows = [
        [
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
        ],
        [
            "Transfers",
            "Data",
            "Bonds",
            "USD",
            "BND",
            "2024-01-10",
            "In",
            "5",
            "500",
            "",
        ],
    ]
    model = _model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, _ = parse_transfers(model)

    assert out == []
    assert any(
        "Transfers: skipped 1 non-stock row(s)" in r.getMessage()
        for r in caplog.records
    )


def test_transfers_column_variant_logged_once_per_subtable(caplog):
    # Header-only subtable using the alternate column names; the variant is surfaced
    # from the header, independent of any rows.
    rows = [
        [
            "Transfers",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date",
            "Direction",
            "Quantity",  # variant of "Qty"
            "Cost Basis",  # variant of "Market Value"
            "Code",
        ],
    ]
    model = _model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        parse_transfers(model)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("using 'Quantity' column (no 'Qty')" in m for m in msgs)
    assert any("using 'Cost Basis' column (no 'Market Value')" in m for m in msgs)
