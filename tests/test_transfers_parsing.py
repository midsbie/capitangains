"""Comprehensive unit tests for IBKR Transfers parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_transfers
"""

import datetime as dt
from decimal import Decimal

import pytest

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import parse_transfers


def _parse_rows(rows):
    """Helper to parse CSV rows into IbkrModel."""
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_basic_incoming_transfer():
    """Test parsing a single incoming stock transfer with market value."""
    rows = [
        [
            "Transfers",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date",
            "Type",
            "Direction",
            "Xfer Company",
            "Xfer Account",
            "Qty",
            "Xfer Price",
            "Market Value",
            "Realized P/L",
            "Cash Amount",
            "Code",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "AZN",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "10",
            "--",
            "881.40",
            "0.00",
            "0.00",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    t = transfers[0]
    assert t.section == "Transfers"
    assert t.asset_category == "Stocks"
    assert t.currency == "GBP"
    assert t.symbol == "AZN"
    assert t.date == dt.date(2021, 10, 15)
    assert t.direction == "In"
    assert t.quantity == Decimal("10")
    assert t.market_value == Decimal("881.40")
    assert t.code == ""


def test_parse_outgoing_transfer():
    """Test parsing outgoing stock transfer (market value optional)."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-06-01",
            "Out",
            "50",
            "7500.00",
            "O",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    t = transfers[0]
    assert t.direction == "Out"
    assert t.symbol == "AAPL"
    assert t.quantity == Decimal("50")
    assert t.market_value == Decimal("7500.00")


def test_parse_outgoing_transfer_without_market_value():
    """Test that OUT transfers work even without market value."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-06-01",
            "Out",
            "50",
            "",  # Empty market value for OUT is OK
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    t = transfers[0]
    assert t.direction == "Out"
    assert t.market_value == Decimal("0")  # Defaults to 0 for OUT


def test_parse_multiple_transfers_sorted_by_date():
    """Test parsing multiple transfers and verify date sorting."""
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
        # Later date
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2021-12-15",
            "In",
            "25",
            "25000.00",
            "",
        ],
        # Earlier date
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "AZN",
            "2021-01-10",
            "In",
            "100",
            "8000.00",
            "",
        ],
        # Middle date
        [
            "Transfers",
            "Data",
            "Stocks",
            "EUR",
            "BMW",
            "2021-06-20",
            "In",
            "50",
            "4500.00",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 3
    # Should be sorted by date
    assert transfers[0].date == dt.date(2021, 1, 10)
    assert transfers[0].symbol == "AZN"
    assert transfers[1].date == dt.date(2021, 6, 20)
    assert transfers[1].symbol == "BMW"
    assert transfers[2].date == dt.date(2021, 12, 15)
    assert transfers[2].symbol == "TSLA"


def test_parse_quantity_with_commas():
    """Test parsing quantities with thousand separators (e.g., '2,500')."""
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
            "Stocks",
            "GBP",
            "LLOY",
            "2021-10-15",
            "In",
            "2,500",  # Quantity with comma
            "1,211.00",  # Market value with comma
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    assert transfers[0].quantity == Decimal("2500")
    assert transfers[0].market_value == Decimal("1211.00")


def test_parse_different_asset_categories():
    """Test parsing transfers for different stock-like asset categories."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "10",
            "1500",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stock",
            "USD",
            "GOOGL",
            "2021-01-02",
            "In",
            "5",
            "12000",
            "",
        ],
        [
            "Transfers",
            "Data",
            "ETF",
            "USD",
            "SPY",
            "2021-01-03",
            "In",
            "20",
            "8000",
            "",
        ],
        [
            "Transfers",
            "Data",
            "ETFs",
            "USD",
            "QQQ",
            "2021-01-04",
            "In",
            "15",
            "5000",
            "",
        ],
        [
            "Transfers",
            "Data",
            "ETP",
            "USD",
            "GLD",
            "2021-01-05",
            "In",
            "10",
            "1700",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 5
    assert transfers[0].asset_category == "Stocks"
    assert transfers[1].asset_category == "Stock"
    assert transfers[2].asset_category == "ETF"
    assert transfers[3].asset_category == "ETFs"
    assert transfers[4].asset_category == "ETP"


def test_parse_case_insensitive_direction():
    """Test that direction matching is case-insensitive."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "IN",
            "10",
            "1500",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2021-01-02",
            "in",
            "5",
            "12000",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2021-01-03",
            "OUT",
            "20",
            "",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "MSFT",
            "2021-01-04",
            "out",
            "15",
            "",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 4
    assert all(t.direction in ["IN", "in", "OUT", "out"] for t in transfers)


def test_parse_with_quantity_column_alternative():
    """Test that 'Quantity' column works as fallback to 'Qty'."""
    rows = [
        [
            "Transfers",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date",
            "Direction",
            "Quantity",  # Alternative column name
            "Market Value",
            "Code",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "100",
            "15000",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    assert transfers[0].quantity == Decimal("100")


def test_parse_with_cost_basis_column():
    """Test that 'Cost Basis' column is used as fallback to 'Market Value'."""
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
            "Cost Basis",  # Alternative to Market Value
            "Code",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "50",
            "7500.00",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    assert len(transfers) == 1
    assert transfers[0].market_value == Decimal("7500.00")


# =============================================================================
# Filtering Tests
# =============================================================================


def test_skip_non_stock_asset_categories():
    """Test that non-stock-like asset categories are skipped."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "10",
            "1500",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Forex",
            "USD",
            "EUR.USD",
            "2021-01-01",
            "In",
            "1000",
            "1200",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Options",
            "USD",
            "AAPL C 150",
            "2021-01-01",
            "In",
            "10",
            "500",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Bonds",
            "USD",
            "BOND123",
            "2021-01-01",
            "In",
            "5",
            "5000",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    # Only the Stocks transfer should be included
    assert len(transfers) == 1
    assert transfers[0].asset_category == "Stocks"
    assert transfers[0].symbol == "AAPL"


def test_skip_total_rows():
    """Test that 'Total' rows (no symbol) are skipped."""
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
            "Stocks",
            "GBP",
            "AZN",
            "2021-10-15",
            "In",
            "10",
            "881.40",
            "",
        ],
        ["Transfers", "Data", "Total", "", "", "", "", "", "4107.27", ""],
        ["Transfers", "Data", "Total in EUR", "", "", "", "", "", "4868.757858", ""],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    # Total rows should be skipped
    assert len(transfers) == 1
    assert transfers[0].symbol == "AZN"


# =============================================================================
# Error Condition Tests
# =============================================================================


def test_error_missing_symbol():
    """Test that missing symbol raises ValueError."""
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
            "Stocks",
            "USD",
            "",  # Missing symbol
            "2021-01-01",
            "In",
            "10",
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Invalid transfer row: missing"):
        parse_transfers(model)


def test_error_missing_currency():
    """Test that missing currency raises ValueError."""
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
            "Stocks",
            "",  # Missing currency
            "AAPL",
            "2021-01-01",
            "In",
            "10",
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Invalid transfer row: missing"):
        parse_transfers(model)


def test_error_missing_date():
    """Test that missing date raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "",  # Missing date
            "In",
            "10",
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Invalid transfer row: missing"):
        parse_transfers(model)


def test_error_missing_direction():
    """Test that missing direction raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "",  # Missing direction
            "10",
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Invalid transfer row: missing"):
        parse_transfers(model)


def test_error_missing_quantity():
    """Test that missing quantity raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "",  # Missing quantity
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Invalid transfer row: missing"):
        parse_transfers(model)


def test_error_invalid_direction():
    """Test that invalid direction (not 'In' or 'Out') raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "Transfer",  # Invalid direction
            "10",
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Unsupported transfer direction"):
        parse_transfers(model)


def test_error_zero_quantity():
    """Test that zero quantity raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "0",  # Zero quantity
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Transfer quantity must be positive"):
        parse_transfers(model)


def test_error_negative_quantity():
    """Test that negative quantity raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "-10",  # Negative quantity
            "1500",
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Transfer quantity must be positive"):
        parse_transfers(model)


def test_error_incoming_transfer_missing_market_value():
    """Test that IN transfer without market value raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "10",
            "",  # Missing market value for IN transfer
            "",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="Transfer IN.*is missing Market Value"):
        parse_transfers(model)


def test_error_incoming_transfer_invalid_market_value():
    """Test that IN transfer with invalid market value format raises ValueError."""
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
            "Stocks",
            "USD",
            "AAPL",
            "2021-01-01",
            "In",
            "10",
            "--",  # Placeholder/invalid market value
            "",
        ],
    ]

    model = _parse_rows(rows)
    # Should raise ValueError when trying to parse "--" as decimal
    with pytest.raises(ValueError):
        parse_transfers(model)


# =============================================================================
# Real-World Data Test
# =============================================================================


def test_parse_real_world_data():
    """Test parsing real-world data from U6994737_2021_2021.csv."""
    rows = [
        [
            "Transfers",
            "Header",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date",
            "Type",
            "Direction",
            "Xfer Company",
            "Xfer Account",
            "Qty",
            "Xfer Price",
            "Market Value",
            "Realized P/L",
            "Cash Amount",
            "Code",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "AZN",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "10",
            "--",
            "881.40",
            "0.00",
            "0.00",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "EZJ",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "165",
            "--",
            "1,040.49",
            "0.00",
            "0.00",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "IAG",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "550",
            "--",
            "974.38",
            "0.00",
            "0.00",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "GBP",
            "LLOY",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "2,500",
            "--",
            "1,211.00",
            "0.00",
            "0.00",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Total",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "4107.27",
            "0",
            "0",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Total in EUR",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "4868.757858",
            "0",
            "0",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Stocks",
            "USD",
            "FRONU",
            "2021-10-15",
            "Internal",
            "In",
            "--",
            "U4842277",
            "660",
            "--",
            "6,534.00",
            "0.00",
            "0.00",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Total",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "6534",
            "0",
            "0",
            "",
        ],
        [
            "Transfers",
            "Data",
            "Total in EUR",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "5633.02674",
            "0",
            "0",
            "",
        ],
    ]

    model = _parse_rows(rows)
    transfers = parse_transfers(model)

    # Should parse 5 stock transfers and skip 4 "Total" rows
    assert len(transfers) == 5

    # All should be from same date
    assert all(t.date == dt.date(2021, 10, 15) for t in transfers)

    # All should be incoming
    assert all(t.direction == "In" for t in transfers)

    # Verify specific transfers
    azn = next(t for t in transfers if t.symbol == "AZN")
    assert azn.currency == "GBP"
    assert azn.quantity == Decimal("10")
    assert azn.market_value == Decimal("881.40")

    lloy = next(t for t in transfers if t.symbol == "LLOY")
    assert lloy.quantity == Decimal("2500")  # Comma removed
    assert lloy.market_value == Decimal("1211.00")  # Comma removed

    fronu = next(t for t in transfers if t.symbol == "FRONU")
    assert fronu.currency == "USD"
    assert fronu.quantity == Decimal("660")
    assert fronu.market_value == Decimal("6534.00")  # Comma removed
