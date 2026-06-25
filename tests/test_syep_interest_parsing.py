"""Comprehensive unit tests for IBKR SYEP Interest Details parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_syep_interest_details
"""

import datetime as dt
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import parse_syep_interest_details
from tests.support import parse_model

# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_complete_syep_row():
    """Test parsing a complete SYEP interest row with all fields."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    s = syep[0]
    assert s.currency == Currency("USD")
    assert s.value_date == dt.date(2024, 1, 15)
    assert s.symbol == "AAPL"
    assert s.start_date == dt.date(2024, 1, 1)
    assert s.quantity == Decimal("1000")
    assert s.collateral_amount == Decimal("150000.00")
    assert s.market_rate_pct == Decimal("5.50")
    assert s.customer_rate_pct == Decimal("4.75")
    assert s.interest_paid == Decimal("195.83")
    assert s.code == "SL"


def test_parse_multiple_syep_rows():
    """Test parsing multiple SYEP interest entries."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-02-15",
            "GOOGL",
            "2024-02-01",
            "500",
            "70000.00",
            "6.00",
            "5.25",
            "122.92",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 2
    assert syep[0].symbol == "AAPL"
    assert syep[1].symbol == "GOOGL"


def test_parse_optional_value_date_empty():
    """Test that empty value_date results in None."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "",  # Empty value date
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    assert syep[0].value_date is None


def test_parse_optional_start_date_empty():
    """Test that empty start_date results in None."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "",  # Empty start date
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    assert syep[0].start_date is None


def test_parse_percentage_fields_as_decimal():
    """Test that percentage fields are stored as Decimal (not divided)."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    # Percentages stored as-is (not divided by 100)
    assert syep[0].market_rate_pct == Decimal("5.50")
    assert syep[0].customer_rate_pct == Decimal("4.75")


# =============================================================================
# Total Filtering Tests
# =============================================================================


def test_skip_total_row():
    """A 'Total' summary row (IBKR labels it in the Currency cell) is skipped."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "Total",  # IBKR labels the running total in the Currency cell
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "195.83",
            "",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)

    # Total row should be skipped, not surfaced as a defect.
    assert len(syep) == 1
    assert syep[0].symbol == "AAPL"
    assert not defects


def test_data_row_missing_currency_is_a_defect_not_a_skipped_trailer():
    """A column-shifted row that lost its Currency fails closed, it does not vanish.

    Regression (F13): the trailer filter keyed on an empty Currency, so a real
    disposal whose Currency cell was lost to a column shift, yet still carrying
    quantity/collateral/rate/interest data, was silently dropped as if it were a
    'Total' trailer instead of surfacing as a defect. Empty Currency alone no longer
    means trailer; only a 'Total'-labelled or fully blank row is skipped.
    """
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "",  # Currency lost to a column shift; the rest is real data
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)

    assert not syep
    assert defects
    assert "Currency" in defects[0].reason
    assert defects[0].symbol == "AAPL"
    assert defects[0].date == "2024-01-15"


def test_skip_total_in_eur_rows():
    """Test that 'Total in EUR' rows are skipped."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "Total in EUR",  # Total in EUR row
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "168.50",
            "",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    # Total in EUR row should be skipped
    assert len(syep) == 1


# =============================================================================
# Numeric Field Validation Tests
# =============================================================================


def test_error_missing_quantity():
    """Test that missing quantity raises ValueError."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "",  # Missing quantity
            "150000.00",
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)
    assert defects
    assert "missing numeric fields" in defects[0].reason
    assert not syep


def test_error_missing_collateral_amount():
    """Test that missing collateral amount raises ValueError."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "",  # Missing collateral amount
            "5.50",
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)
    assert defects
    assert "missing numeric fields" in defects[0].reason
    assert not syep


def test_error_missing_market_rate():
    """Test that missing market rate raises ValueError."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "",  # Missing market rate
            "4.75",
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)
    assert defects
    assert "missing numeric fields" in defects[0].reason
    assert not syep


def test_error_missing_customer_rate():
    """Test that missing customer rate raises ValueError."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "",  # Missing customer rate
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)
    assert defects
    assert "missing numeric fields" in defects[0].reason
    assert not syep


def test_error_missing_interest_paid():
    """Test that missing interest paid raises ValueError."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.50",
            "4.75",
            "",  # Missing interest paid
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, defects = parse_syep_interest_details(model)
    assert defects
    assert "missing numeric fields" in defects[0].reason
    assert not syep


def test_parse_numeric_fields_with_thousand_separators():
    """Test parsing numeric fields with comma thousand separators."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "10,000",  # Comma separator
            "1,500,000.00",  # Comma separators
            "5.50",
            "4.75",
            "1,958.33",  # Comma separator
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    assert syep[0].quantity == Decimal("10000")
    assert syep[0].collateral_amount == Decimal("1500000.00")
    assert syep[0].interest_paid == Decimal("1958.33")


def test_parse_decimal_percentages():
    """Test parsing percentage fields with decimal precision."""
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
            "Currency",
            "Value Date",
            "Symbol",
            "Start Date",
            "Quantity",
            "Collateral Amount",
            "Market-based Rate (%)",
            "Interest Rate on Customer Collateral (%)",
            "Interest Paid to Customer",
            "Code",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL",
            "2024-01-01",
            "1000",
            "150000.00",
            "5.123456",  # High precision percentage
            "4.789012",  # High precision percentage
            "195.83",
            "SL",
        ],
    ]

    model = parse_model(rows)
    syep, _ = parse_syep_interest_details(model)

    assert len(syep) == 1
    assert syep[0].market_rate_pct == Decimal("5.123456")
    assert syep[0].customer_rate_pct == Decimal("4.789012")
