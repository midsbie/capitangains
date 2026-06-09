"""Diagnostics emitted by the extractors: aggregate skip/elision counts, the material
warning when a whole trades subtable is dropped, and the per-row defects that reject a
malformed cash-flow line outright.

The skip/elision counts and the subtable warning were previously silent (or debug-only)
and now surface so a run never drops data without saying so. The cash-flow defect cases
assert the stronger contract: an incomplete (non-trailer) row halts the run rather than
being skipped at all.
"""

import logging

from capitangains.reporting.extract import (
    parse_dividends,
    parse_interest,
    parse_trades_stocklike,
    parse_transfers,
    parse_withholding_tax,
)
from tests.support import TRADES_COLUMNS, header_row, parse_model, trade_data

_EXTRACT_LOGGER = "capitangains.reporting.extract"

_TRADE_HEADER = header_row("Trades", TRADES_COLUMNS)


def _trade_data(asset, symbol, *, basis="1000", realized="0"):
    return trade_data(
        asset_category=asset, symbol=symbol, basis=basis, realized=realized
    )


def test_trades_out_of_scope_rows_counted_as_info(caplog):
    model = parse_model(
        [_TRADE_HEADER, _trade_data("Stocks", "AAPL"), _trade_data("Bonds", "BND")]
    )
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        trades, _ = parse_trades_stocklike(model, asset_scope="stocks_etfs")

    assert [t.symbol for t in trades] == ["AAPL"]  # bond filtered out
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Trades (scope='stocks_etfs'): skipped 1 row(s)" in m for m in msgs)


def test_trades_elided_basis_counted_as_info(caplog):
    model = parse_model([_TRADE_HEADER, _trade_data("Stocks", "AAPL", basis="...")])
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
    model = parse_model(rows)
    with caplog.at_level(logging.WARNING, logger=_EXTRACT_LOGGER):
        trades, _ = parse_trades_stocklike(model, asset_scope="stocks_etfs")

    assert trades == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "missing required column(s) ['Proceeds']" in warnings[0].getMessage()


def test_dividends_missing_date_is_a_defect(caplog):
    # A row with a real Currency/Amount but no Date is not a trailer: the Date sets the
    # tax year, FX rate and FIFO order, so its absence is corruption (or an unrecognized
    # format that breaks our assumptions), surfaced as a defect rather than tolerated.
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "EUR", "", "Div XYZ", "5.00"],  # missing date
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, defects = parse_dividends(model)

    assert len(out) == 1
    assert [d.reason for d in defects] == ["Invalid Dividends row: missing Date"]
    assert not any("skipped" in r.getMessage() for r in caplog.records)


def test_dividends_blank_description_is_a_defect(caplog):
    # A row with currency+date+amount but a blank Description is a taxable amount with
    # no descriptor: corruption, not a non-data trailer. It must surface as a defect
    # that halts at the boundary, never vanish via a silent "incomplete" skip.
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "EUR", "2024-01-06", "", "5.00"],  # blank description
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, defects = parse_dividends(model)

    assert len(out) == 1
    assert [(d.date, d.reason) for d in defects] == [
        ("2024-01-06", "Invalid Dividends row: missing Description")
    ]
    assert not any("skipped" in r.getMessage() for r in caplog.records)


def test_dividends_blank_currency_is_a_defect(caplog):
    # Currency selects the FX rate, so a dated row with a blank Currency is corruption,
    # not a trailer: it must surface as a defect, symmetric with a blank Description.
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "", "2024-01-06", "Div NOCUR", "5.00"],  # blank currency
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, defects = parse_dividends(model)

    assert len(out) == 1
    assert [(d.date, d.reason) for d in defects] == [
        ("2024-01-06", "Invalid Dividends row: missing Currency")
    ]
    assert not any("skipped" in r.getMessage() for r in caplog.records)


def test_dividends_total_rows_surface_at_debug_not_info(caplog):
    # A recognized Total/subtotal trailer is expected, redundant, non-data: it is
    # reported once at DEBUG (never fully silent) and never counted as "incomplete".
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "Total", "", "", "10.00"],
        ["Dividends", "Data", "Total in EUR", "", "", "9.00"],
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.DEBUG, logger=_EXTRACT_LOGGER):
        out, defects = parse_dividends(model)

    assert len(out) == 1
    assert not defects
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Dividends: skipped 2 summary/total row(s)" in m for m in msgs)
    assert not any("incomplete" in m for m in msgs)


def test_dividends_dateless_currencyless_row_with_content_is_a_defect(caplog):
    # A row that lost both Date and Currency but still carries a Description and Amount
    # is corruption, not the fully blank separator IBKR emits between subtables: only an
    # all-blank trailer is dropped, so this must surface as a defect, never vanish.
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
        ["Dividends", "Data", "", "", "Div ORPHAN", "5.00"],  # no date, no currency
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.DEBUG, logger=_EXTRACT_LOGGER):
        out, defects = parse_dividends(model)

    assert len(out) == 1
    assert [(d.date, d.reason) for d in defects] == [
        (None, "Invalid Dividends row: missing Date, Currency")
    ]
    assert not any("skipped" in r.getMessage() for r in caplog.records)


def test_withholding_missing_date_is_a_defect(caplog):
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
    model = parse_model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        out, defects = parse_withholding_tax(model)

    assert len(out) == 1
    assert [d.reason for d in defects] == ["Invalid Withholding Tax row: missing Date"]
    assert not any("skipped" in r.getMessage() for r in caplog.records)


def test_interest_missing_date_is_a_defect_total_stays_debug(caplog):
    # The two date-less rows must not be conflated: the Stray row carries a real
    # currency, so its missing Date is a defect; the Total is a recognized trailer,
    # reported only at DEBUG and never as a defect.
    rows = [
        ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
        ["Interest", "Data", "USD", "2024-01-05", "Credit Interest", "2.00"],
        ["Interest", "Data", "USD", "", "Stray Interest", "1.00"],  # missing date
        ["Interest", "Data", "Total", "", "", "10.00"],  # recognized total
    ]
    model = parse_model(rows)
    with caplog.at_level(logging.DEBUG, logger=_EXTRACT_LOGGER):
        out, defects = parse_interest(model)

    assert len(out) == 1
    assert [d.reason for d in defects] == ["Invalid Interest row: missing Date"]
    assert any(
        "Interest: skipped 1 summary/total row(s)" in r.getMessage()
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
    model = parse_model(rows)
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
    model = parse_model(rows)
    with caplog.at_level(logging.INFO, logger=_EXTRACT_LOGGER):
        parse_transfers(model)

    msgs = [r.getMessage() for r in caplog.records]
    assert any("using 'Quantity' column (no 'Qty')" in m for m in msgs)
    assert any("using 'Cost Basis' column (no 'Market Value')" in m for m in msgs)
