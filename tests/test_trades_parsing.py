"""Comprehensive unit tests for IBKR Trades parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_trades_stocklike
and parse_trades_stocklike_row
"""

import datetime as dt
from decimal import Decimal

import pytest

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import parse_trades_stocklike


def _parse_rows(rows):
    """Helper to parse CSV rows into IbkrModel."""
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_basic_buy_trade():
    """Test parsing a simple buy trade with all required fields."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:30:00",
            "100",
            "150.50",
            "-15050.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    t = trades[0]
    assert t.section == "Trades"
    assert t.asset_category == "Stocks"
    assert t.currency == "USD"
    assert t.symbol == "AAPL"
    assert t.datetime_str == "2024-01-15, 10:30:00"
    assert t.date == dt.date(2024, 1, 15)
    assert t.quantity == Decimal("100")
    assert t.t_price == Decimal("150.50")
    assert t.proceeds == Decimal("-15050.00")
    assert t.comm_fee == Decimal("-1.00")
    assert t.code == "P"
    assert t.basis_ccy is None
    assert t.realized_pl_ccy is None


def test_parse_basic_sell_trade():
    """Test parsing a sell trade (negative quantity)."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2024-02-20, 14:45:00",
            "-50",
            "200.00",
            "10000.00",
            "-2.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    t = trades[0]
    assert t.quantity == Decimal("-50")  # Negative for sell
    assert t.proceeds == Decimal("10000.00")  # Positive for sell
    assert t.comm_fee == Decimal("-2.00")


def test_parse_trade_with_optional_basis_and_realized_pl():
    """Test parsing trade with optional Basis and Realized P/L columns."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
            "Basis",
            "Realized P/L",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2024-03-10, 11:00:00",
            "-25",
            "140.00",
            "3500.00",
            "-1.50",
            "P",
            "-3000.00",
            "498.50",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    t = trades[0]
    assert t.basis_ccy == Decimal("-3000.00")
    assert t.realized_pl_ccy == Decimal("498.50")


def test_parse_trade_without_optional_fields():
    """Test that trades without Basis/Realized P/L set them to None."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "EUR",
            "BMW",
            "2024-04-05, 09:00:00",
            "30",
            "90.00",
            "-2700.00",
            "-3.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].basis_ccy is None
    assert trades[0].realized_pl_ccy is None


def test_parse_multiple_trades_sorted_by_date():
    """Test parsing multiple trades and verify sorting by date."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        # Later date
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "MSFT",
            "2024-03-15, 10:00:00",
            "100",
            "400.00",
            "-40000.00",
            "-5.00",
            "P",
        ],
        # Earlier date
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-10, 09:00:00",
            "50",
            "150.00",
            "-7500.00",
            "-2.00",
            "P",
        ],
        # Middle date
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "NVDA",
            "2024-02-20, 12:00:00",
            "75",
            "500.00",
            "-37500.00",
            "-7.50",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 3
    # Should be sorted by date
    assert trades[0].date == dt.date(2024, 1, 10)
    assert trades[0].symbol == "AAPL"
    assert trades[1].date == dt.date(2024, 2, 20)
    assert trades[1].symbol == "NVDA"
    assert trades[2].date == dt.date(2024, 3, 15)
    assert trades[2].symbol == "MSFT"


def test_parse_same_date_buys_before_sells():
    """Test that buys are sorted before sells on the same date."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        # Sell (should come second)
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 14:00:00",
            "-100",
            "151.00",
            "15100.00",
            "-2.00",
            "P",
        ],
        # Buy (should come first)
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 2
    # Buy (positive qty) should come first
    assert trades[0].quantity > 0
    assert trades[0].datetime_str == "2024-01-15, 10:00:00"
    # Sell (negative qty) should come second
    assert trades[1].quantity < 0
    assert trades[1].datetime_str == "2024-01-15, 14:00:00"


def test_parse_commission_from_comm_fee_column():
    """Test parsing commission from 'Comm/Fee' column."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "-15000.00",
            "-5.50",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].comm_fee == Decimal("-5.50")


def test_parse_commission_from_comm_in_eur_column():
    """Test parsing commission from 'Comm in EUR' column (fallback)."""
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
            "Proceeds",
            "Comm in EUR",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "EUR",
            "BMW",
            "2024-01-15, 10:00:00",
            "50",
            "100.00",
            "-5000.00",
            "-3.25",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].comm_fee == Decimal("-3.25")


def test_parse_different_asset_categories_stocks():
    """Test parsing different stock-like asset categories with 'stocks' scope."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-01, 10:00:00",
            "10",
            "150",
            "-1500",
            "-1",
            "P",
        ],
        [
            "Trades",
            "Data",
            "Stock",
            "USD",
            "GOOGL",
            "2024-01-02, 10:00:00",
            "5",
            "140",
            "-700",
            "-1",
            "P",
        ],
        [
            "Trades",
            "Data",
            "ETF",
            "USD",
            "SPY",
            "2024-01-03, 10:00:00",
            "20",
            "400",
            "-8000",
            "-2",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    # Only "Stocks" and "Stock" should be included
    assert len(trades) == 2
    assert trades[0].asset_category in ["Stocks", "Stock"]
    assert trades[1].asset_category in ["Stocks", "Stock"]


def test_parse_scope_filtering_etfs():
    """Test scope filtering with 'etfs' scope."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-01, 10:00:00",
            "10",
            "150",
            "-1500",
            "-1",
            "P",
        ],
        [
            "Trades",
            "Data",
            "ETF",
            "USD",
            "SPY",
            "2024-01-02, 10:00:00",
            "20",
            "400",
            "-8000",
            "-2",
            "P",
        ],
        [
            "Trades",
            "Data",
            "ETFs",
            "USD",
            "QQQ",
            "2024-01-03, 10:00:00",
            "15",
            "350",
            "-5250",
            "-2",
            "P",
        ],
        [
            "Trades",
            "Data",
            "ETP",
            "USD",
            "GLD",
            "2024-01-04, 10:00:00",
            "10",
            "170",
            "-1700",
            "-1",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="etfs")

    # Only ETF, ETFs, ETP should be included
    assert len(trades) == 3
    assert all(t.asset_category in ["ETF", "ETFs", "ETP"] for t in trades)


# =============================================================================
# Edge Case Tests
# =============================================================================


def test_error_empty_t_price():
    """Empty T.Price on a valid trade row is corrupt input."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "",  # Empty T.Price
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="empty string"):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_parse_zero_quantity_filtered():
    """Test that trades with zero quantity are filtered out."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "0",  # Zero quantity
            "150.00",
            "0.00",
            "0.00",
            "C",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2024-01-15, 11:00:00",
            "50",
            "140.00",
            "-7000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    # Zero quantity trade should be filtered
    assert len(trades) == 1
    assert trades[0].symbol == "GOOGL"


def test_parse_quantities_with_thousand_separators():
    """Test parsing quantities with comma thousand separators."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "1,500",  # Comma separator
            "150.00",
            "-225,000.00",  # Comma separator
            "-10.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].quantity == Decimal("1500")
    assert trades[0].proceeds == Decimal("-225000.00")


def test_parse_proceeds_with_thousand_separators():
    """Test parsing proceeds with comma thousand separators."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "TSLA",
            "2024-01-15, 10:00:00",
            "250",
            "200.00",
            "-50,000.00",
            "-5.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].proceeds == Decimal("-50000.00")


def test_parse_datetime_with_different_formats():
    """Test parsing date/time with different formats."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        # Format with comma separator
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:30:00",
            "100",
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
        # Format without time
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2024-02-20",
            "50",
            "140.00",
            "-7000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 2
    assert trades[0].date == dt.date(2024, 1, 15)
    assert trades[0].datetime_str == "2024-01-15, 10:30:00"
    assert trades[1].date == dt.date(2024, 2, 20)
    assert trades[1].datetime_str == "2024-02-20"


def test_parse_multiple_subtables():
    """Test parsing trades from multiple subtables in Trades section."""
    rows = [
        # First subtable
        [
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
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150",
            "-15000",
            "-1",
            "P",
        ],
        # Second subtable with same structure
        [
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
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2024-01-16, 11:00:00",
            "50",
            "140",
            "-7000",
            "-1",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    # Should parse from both subtables
    assert len(trades) == 2


def test_parse_empty_commission_defaults_to_zero():
    """Test that empty commission field defaults to 0."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "-15000.00",
            "",  # Empty commission
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].comm_fee == Decimal("0")


def test_parse_trade_with_all_optional_fields():
    """Test trade with all optional fields populated."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
            "Basis",
            "Realized P/L",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "-100",
            "155.00",
            "15500.00",
            "-2.00",
            "P",
            "-15000.00",
            "498.00",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    t = trades[0]
    assert t.quantity == Decimal("-100")
    assert t.proceeds == Decimal("15500.00")
    assert t.comm_fee == Decimal("-2.00")
    assert t.basis_ccy == Decimal("-15000.00")
    assert t.realized_pl_ccy == Decimal("498.00")


# =============================================================================
# Error Condition Tests
# =============================================================================


def test_error_missing_symbol():
    """Test that missing symbol raises ValueError."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "",  # Missing symbol
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="missing symbol"):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_error_missing_datetime():
    """Test that missing date/time raises ValueError."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "",  # Missing date/time
            "100",
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    # parse_date on empty string should raise
    with pytest.raises(ValueError):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_error_missing_quantity():
    """Test that missing quantity raises ValueError."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "",  # Missing quantity
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    # to_dec_strict on empty string should raise
    with pytest.raises(ValueError):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_error_missing_proceeds():
    """Test that missing proceeds raises ValueError."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "",  # Missing proceeds
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    # to_dec_strict on empty string should raise
    with pytest.raises(ValueError):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_error_invalid_quantity_format():
    """Test that invalid quantity format raises ValueError."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-15, 10:00:00",
            "invalid",  # Invalid quantity
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError):
        parse_trades_stocklike(model, asset_scope="stocks")


def test_skip_subtable_missing_required_columns():
    """Test that subtables missing required columns are skipped."""
    rows = [
        # Subtable missing "Proceeds" column
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
            "2024-01-15, 10:00:00",
            "100",
            "150",
            "-1",
            "P",
        ],
        # Valid subtable
        [
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
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "GOOGL",
            "2024-01-16, 11:00:00",
            "50",
            "140",
            "-7000",
            "-1",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    # Should only parse from valid subtable
    assert len(trades) == 1
    assert trades[0].symbol == "GOOGL"


def test_filter_non_stock_asset_by_scope():
    """Test that non-matching asset categories are filtered by scope."""
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
            "Proceeds",
            "Comm/Fee",
            "Code",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAPL",
            "2024-01-01, 10:00:00",
            "10",
            "150",
            "-1500",
            "-1",
            "P",
        ],
        [
            "Trades",
            "Data",
            "Options",
            "USD",
            "AAPL C 150",
            "2024-01-02, 10:00:00",
            "1",
            "5",
            "-500",
            "-1",
            "P",
        ],
        [
            "Trades",
            "Data",
            "Forex",
            "USD",
            "EUR.USD",
            "2024-01-03, 10:00:00",
            "1000",
            "1.1",
            "-1100",
            "-1",
            "P",
        ],
    ]

    model = _parse_rows(rows)
    trades = parse_trades_stocklike(model, asset_scope="stocks")

    # Only Stocks should be included
    assert len(trades) == 1
    assert trades[0].asset_category == "Stocks"
    assert trades[0].symbol == "AAPL"
