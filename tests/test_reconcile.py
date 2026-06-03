import logging
from decimal import Decimal

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.reconcile import reconcile_with_ibkr_summary


def _parse_rows(rows):
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


def test_reconcile_collects_stock_symbols_only():
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Symbol",
            "Total",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "ABC",
            "10.00",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Forex",
            "USD",
            "5.00",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "ABC",
            "2.50",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Description",
            "Realized",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "XYZ",
            "15.00",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "LMN",
            "...",
        ],
    ]
    model = _parse_rows(rows)

    result = reconcile_with_ibkr_summary(model)
    assert result == {
        "ABC": Decimal("12.50"),
        "XYZ": Decimal("15.00"),
    }


def test_reconcile_fallback_prefers_rightmost_numeric_column():
    """When no header matches the P&L regex, the fallback should pick the
    rightmost parseable numeric column (scanning right-to-left)."""
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Symbol",
            "Quantity",
            "Amount",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "ABC",
            "100.00",
            "7.50",
        ],
    ]
    model = _parse_rows(rows)

    # "Amount" (rightmost) should win over "Quantity"
    assert reconcile_with_ibkr_summary(model) == {"ABC": Decimal("7.50")}


def test_reconcile_preserves_zero_realized_pl():
    """A legitimate 0.00 realized P/L must be included, not dropped."""
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Symbol",
            "Total",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "ABC",
            "0.00",
        ],
        [
            "Realized & Unrealized Performance Summary",
            "Data",
            "Stocks",
            "XYZ",
            "5.00",
        ],
    ]
    model = _parse_rows(rows)

    result = reconcile_with_ibkr_summary(model)
    assert result == {"ABC": Decimal("0.00"), "XYZ": Decimal("5.00")}


def test_reconcile_returns_empty_when_missing_columns():
    rows = [
        ["Realized & Unrealized Performance Summary", "Header", "Symbol", "Total"],
        ["Realized & Unrealized Performance Summary", "Data", "ABC", "10.00"],
    ]
    model = _parse_rows(rows)

    assert reconcile_with_ibkr_summary(model) == {}


def test_reconcile_symbol_column_fallback_warns(caplog):
    # No Symbol/Ticker/Description column: the parser guesses index 2, which can
    # mis-key every row, so it must warn (default-visible).
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Total",
            "Realized",
        ],
        ["Realized & Unrealized Performance Summary", "Data", "Stocks", "1", "10.00"],
    ]
    model = _parse_rows(rows)

    with caplog.at_level(logging.WARNING, logger="capitangains.reporting.reconcile"):
        reconcile_with_ibkr_summary(model)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no Symbol/Ticker/Description column" in warnings[0].getMessage()


def test_reconcile_skips_subtable_without_usable_symbol_column(caplog):
    # "Asset Category" present (so the subtable is considered) but too few columns to
    # even guess a symbol column: skip it with a default-visible warning, don't crash.
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Total",
        ],
        ["Realized & Unrealized Performance Summary", "Data", "Stocks", "10.00"],
    ]
    model = _parse_rows(rows)

    with caplog.at_level(logging.WARNING, logger="capitangains.reporting.reconcile"):
        result = reconcile_with_ibkr_summary(model)

    assert result == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no usable symbol column" in warnings[0].getMessage()


def test_reconcile_skipped_rows_logged_as_info(caplog):
    rows = [
        [
            "Realized & Unrealized Performance Summary",
            "Header",
            "Asset Category",
            "Symbol",
            "Total",
        ],
        # non-stock, empty symbol, and unparseable value -> three distinct skips
        ["Realized & Unrealized Performance Summary", "Data", "Forex", "USD", "5.00"],
        ["Realized & Unrealized Performance Summary", "Data", "Stocks", "", "3.00"],
        ["Realized & Unrealized Performance Summary", "Data", "Stocks", "ABC", "..."],
    ]
    model = _parse_rows(rows)

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.reconcile"):
        reconcile_with_ibkr_summary(model)

    infos = [r for r in caplog.records if "Reconciliation: skipped" in r.getMessage()]
    assert len(infos) == 1
    assert infos[0].levelno == logging.INFO
    msg = infos[0].getMessage()
    assert "1 non-stock" in msg
    assert "1 empty symbol" in msg
    assert "1 no numeric value" in msg
