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
