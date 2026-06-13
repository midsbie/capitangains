"""Comprehensive unit tests for IBKR Trades parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_trades_stocklike
and parse_trades_stocklike_row
"""

import datetime as dt
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import parse_trades_stocklike
from tests.support import parse_model

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    t = trades[0]
    assert t.section == "Trades"
    assert t.asset_category == "Stocks"
    assert t.currency == Currency("USD")
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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].basis_ccy is None
    assert trades[0].realized_pl_ccy is None


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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="etfs")

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

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert "empty string" in defects[0].reason
    assert not trades  # the corrupt row is rejected, not extracted


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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    # Should parse from both subtables
    assert len(trades) == 2


def test_parse_empty_commission_is_rejected():
    """Empty Comm/Fee is now a data defect, not a silent zero (strict parse).

    Commission feeds basis on buys and net proceeds on sells, so an elided value can no
    longer default to 0 -- the row is rejected and surfaced as an ExtractionDefect.
    """
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

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert not trades  # the row with an elided commission is rejected
    assert defects
    assert "Comm/Fee" in defects[0].reason


def test_parse_zero_commission_is_accepted():
    """A commission-free trade reports '0', which the strict parse accepts with no
    defect."""
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
            "0",  # Commission-free trade
            "P",
        ],
    ]

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert not defects
    assert len(trades) == 1
    assert trades[0].comm_fee == Decimal("0")


def test_accumulates_multiple_defects_without_failing_fast():
    """Two malformed rows yield two defects in one call -- no fail-fast on the first.

    The valid row is still extracted; each bad row is rejected independently so the
    operator sees every defect in a single run.
    """
    header = [
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
    ]
    good = [
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
    ]
    bad_qty = [
        "Trades",
        "Data",
        "Stocks",
        "USD",
        "MSFT",
        "2024-02-15, 10:00:00",
        "",  # missing quantity
        "150.00",
        "-15000.00",
        "-1.00",
        "P",
    ]
    bad_proceeds = [
        "Trades",
        "Data",
        "Stocks",
        "USD",
        "GOOGL",
        "2024-03-15, 10:00:00",
        "100",
        "150.00",
        "",  # missing proceeds
        "-1.00",
        "P",
    ]

    model = parse_model([header, good, bad_qty, bad_proceeds])
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert [t.symbol for t in trades] == ["AAPL"]  # only the valid row survives
    assert len(defects) == 2  # both bad rows are reported, not just the first
    assert {d.symbol for d in defects} == {"MSFT", "GOOGL"}
    assert all(d.section == "Trades" for d in defects)


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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

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

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert "missing symbol" in defects[0].reason
    assert not trades


def test_error_missing_currency():
    """Test that missing currency raises ValueError."""
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
            "",  # Missing currency
            "AAPL",
            "2024-01-15, 10:00:00",
            "100",
            "150.00",
            "-15000.00",
            "-1.00",
            "P",
        ],
    ]

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert "missing currency" in defects[0].reason
    assert not trades


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

    model = parse_model(rows)
    # parse_date on an empty string makes the row a defect, not a fatal traceback
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert not trades


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

    model = parse_model(rows)
    # to_dec_strict on an empty string makes the row a defect, not a fatal traceback
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert not trades


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

    model = parse_model(rows)
    # to_dec_strict on an empty string makes the row a defect, not a fatal traceback
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert not trades


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

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")
    assert defects
    assert not trades


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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    # Should only parse from valid subtable
    assert len(trades) == 1
    assert trades[0].symbol == "GOOGL"


def test_parse_elided_basis_and_realized_pl_treated_as_none():
    """Elided placeholders ('...') in Basis/Realized P/L must yield None, not 0."""
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
            "...",
            "...",
        ],
    ]

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    assert len(trades) == 1
    assert trades[0].basis_ccy is None
    assert trades[0].realized_pl_ccy is None


def test_elided_basis_realized_placeholders_and_blanks_map_to_none():
    """Every legitimate elision keeps mapping Basis/Realized P/L to None, silently.

    Guards the malformed-field rejection from over-correcting: a known placeholder
    ('-', '--', 'N/A', 'n/a') or a blank cell is a real "IBKR reported no value" and
    must stay None with no defect; only a genuinely malformed cell becomes a defect.
    """
    header = [
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
    elisions = [("AAA", "-", "--"), ("BBB", "N/A", "n/a"), ("CCC", "", "")]
    rows = [header]
    for i, (symbol, basis, realized) in enumerate(elisions):
        rows.append(
            [
                "Trades",
                "Data",
                "Stocks",
                "USD",
                symbol,
                f"2024-01-1{i}, 10:00:00",
                "-100",
                "155.00",
                "15500.00",
                "-2.00",
                "P",
                basis,
                realized,
            ]
        )

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert not defects
    assert len(trades) == 3
    assert all(t.basis_ccy is None and t.realized_pl_ccy is None for t in trades)


def test_malformed_basis_is_rejected_not_treated_as_elided():
    """A corrupt Basis must surface as a defect, not be silently nulled as "elided".

    Only the known placeholder set ('...', '-', etc.) means "IBKR reported no basis"
    (-> None, which legitimately feeds gap synthesis). A genuinely malformed value --
    here a column-shift artifact that survives comma-stripping but is not a number -- is
    corruption, and like every other strict trade field it must be rejected so the
    boundary halts. Realized P/L below is valid, so only Basis can trigger the defect.
    """
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
            "19,8X7.919",  # malformed: not a placeholder, not a number
            "498.00",
        ],
    ]

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert not trades  # rejected, not extracted with basis_ccy=None
    assert defects
    assert "Basis" in defects[0].reason


def test_malformed_realized_pl_is_rejected_not_treated_as_elided():
    """A corrupt Realized P/L must surface as a defect, mirroring the Basis path.

    Realized P/L uses the same strict parse; a malformed value is currently swallowed to
    None (treated as elided), which then drives the reconciliation and gap paths on
    corrupt data. Basis above is valid, so only Realized P/L can trigger the defect.
    """
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
            "C;P",  # malformed: a Code value column-shifted into Realized P/L
        ],
    ]

    model = parse_model(rows)
    trades, defects = parse_trades_stocklike(model, asset_scope="stocks")

    assert not trades
    assert defects
    assert "Realized P/L" in defects[0].reason


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

    model = parse_model(rows)
    trades, _ = parse_trades_stocklike(model, asset_scope="stocks")

    # Only Stocks should be included
    assert len(trades) == 1
    assert trades[0].asset_category == "Stocks"
    assert trades[0].symbol == "AAPL"
