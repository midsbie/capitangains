"""Comprehensive unit tests for IBKR Dividends parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_dividends
"""

import datetime as dt
from decimal import Decimal

import pytest

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import DividendRow, parse_dividends


def _parse_rows(rows):
    """Helper to parse CSV rows into IbkrModel."""
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_basic_dividend():
    """Test parsing a basic dividend row with all fields."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL (US0378331005) Cash Dividend USD 0.24 per Share (Ordinary Dividend)",
            "24.00",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    d = dividends[0]
    assert d.currency == "USD"
    assert d.date == dt.date(2024, 1, 15)
    assert "AAPL" in d.description
    assert d.amount == Decimal("24.00")
    assert d.amount_eur is None


def test_parse_multiple_dividends():
    """Test parsing multiple dividend entries."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "24.00",
        ],
        [
            "Dividends",
            "Data",
            "EUR",
            "2024-02-20",
            "BMW Dividend Payment",
            "50.00",
        ],
        [
            "Dividends",
            "Data",
            "GBP",
            "2024-03-10",
            "BP Dividend Distribution",
            "15.50",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 3
    assert dividends[0].currency == "USD"
    assert dividends[1].currency == "EUR"
    assert dividends[2].currency == "GBP"


def test_parse_amount_with_thousand_separator():
    """Test parsing dividend amounts with comma thousand separators."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "Large Dividend Distribution",
            "1,250.50",  # Comma separator
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    assert dividends[0].amount == Decimal("1250.50")


def test_parse_amount_with_high_precision():
    """Test parsing dividend amounts with high decimal precision."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "EUR",
            "2024-01-15",
            "Precise Dividend Payment",
            "123.456789",  # High precision
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    assert dividends[0].amount == Decimal("123.456789")


# =============================================================================
# Total Filtering Tests
# =============================================================================


def test_skip_rows_with_empty_currency():
    """Test that rows with empty currency are skipped (total rows)."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "24.00",
        ],
        [
            "Dividends",
            "Data",
            "",  # Empty currency (total row)
            "",
            "Total",
            "24.00",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    # Only the first row should be parsed
    assert len(dividends) == 1
    assert dividends[0].currency == "USD"


def test_skip_rows_with_empty_date():
    """Test that rows with empty date are skipped."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "24.00",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "",  # Empty date
            "Some Description",
            "10.00",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    assert dividends[0].date == dt.date(2024, 1, 15)


def test_skip_rows_with_empty_description():
    """Test that rows with empty description are skipped."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "24.00",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-16",
            "",  # Empty description
            "15.00",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    assert dividends[0].description != ""


def test_skip_total_in_eur_rows():
    """Test that 'Total in EUR' summary rows are skipped."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "24.00",
        ],
        [
            "Dividends",
            "Data",
            "GBP",
            "2024-02-20",
            "BP Dividend",
            "15.00",
        ],
        [
            "Dividends",
            "Data",
            "Total in EUR",  # Total row with currency-like field
            "",
            "",
            "35.50",
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    # Total in EUR row should be skipped (empty date and description)
    assert len(dividends) == 2


# =============================================================================
# Error Condition Tests
# =============================================================================


def test_error_invalid_amount_format():
    """Test that invalid amount format raises ValueError."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "invalid",  # Invalid amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError):
        parse_dividends(model)


def test_error_empty_amount():
    """Test that empty amount raises ValueError (to_dec_strict)."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend",
            "",  # Empty amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="empty string"):
        parse_dividends(model)


def test_error_invalid_date_format():
    """Test that invalid date format raises ValueError."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "invalid-date",  # Invalid date
            "AAPL Cash Dividend",
            "24.00",
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError):
        parse_dividends(model)


def test_parse_negative_dividend_amount():
    """Test parsing negative dividend amounts (reversal/correction)."""
    rows = [
        [
            "Dividends",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Dividends",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend Reversal",
            "-24.00",  # Negative amount
        ],
    ]

    model = _parse_rows(rows)
    dividends = parse_dividends(model)

    assert len(dividends) == 1
    assert dividends[0].amount == Decimal("-24.00")
