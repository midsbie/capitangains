"""Comprehensive unit tests for IBKR Interest parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_interest
"""

import datetime as dt
from decimal import Decimal

import pytest

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import parse_interest


def _parse_rows(rows):
    """Helper to parse CSV rows into IbkrModel."""
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_regular_credit_interest():
    """Test parsing regular credit interest row."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest for Jan-2024",
            "5.25",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    i = interest[0]
    assert i.currency == "EUR"
    assert i.date == dt.date(2024, 1, 31)
    assert "Credit Interest" in i.description
    assert i.amount == Decimal("5.25")
    assert i.amount_eur is None


def test_parse_debit_interest():
    """Test parsing debit interest (negative amount)."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-02-29",
            "USD Debit Interest for Feb-2024",
            "-12.50",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].amount == Decimal("-12.50")


def test_parse_syep_summary_row():
    """Test parsing SYEP interest summary row (mixed content type)."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-01-31",
            "Stock Yield Enhancement Program Interest Summary",
            "195.83",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    i = interest[0]
    assert "Stock Yield Enhancement Program" in i.description
    assert i.amount == Decimal("195.83")


def test_parse_multiple_interest_entries():
    """Test parsing multiple interest entries (mixed types)."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest for Jan-2024",
            "5.25",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-01-31",
            "USD Credit Interest for Jan-2024",
            "8.50",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-01-31",
            "Stock Yield Enhancement Program Interest Summary",
            "195.83",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 3
    assert interest[0].currency == "EUR"
    assert interest[1].currency == "USD"
    assert "Stock Yield Enhancement Program" in interest[2].description


# =============================================================================
# Total Filtering Tests
# =============================================================================


def test_skip_total_rows_empty_currency():
    """Test that rows with empty currency are skipped (total rows)."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest for Jan-2024",
            "5.25",
        ],
        [
            "Interest",
            "Data",
            "",  # Empty currency
            "",
            "Total",
            "5.25",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    # Total row should be skipped
    assert len(interest) == 1
    assert interest[0].currency == "EUR"


def test_skip_total_in_eur_rows():
    """Test that 'Total in EUR' rows are skipped."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-01-31",
            "USD Credit Interest",
            "8.50",
        ],
        [
            "Interest",
            "Data",
            "Total in EUR",  # Total in EUR row
            "",
            "",
            "7.32",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    # Total in EUR row should be skipped (starts with "total")
    assert len(interest) == 1


def test_skip_rows_with_total_prefix():
    """Test that rows with currency starting with 'total' are skipped."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "5.25",
        ],
        [
            "Interest",
            "Data",
            "Total",  # Currency field = "Total"
            "",
            "",
            "5.25",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].currency == "EUR"


def test_skip_rows_with_empty_date():
    """Test that rows with empty date are skipped."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "5.25",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "",  # Empty date
            "Some Description",
            "10.00",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].date == dt.date(2024, 1, 31)


def test_skip_rows_with_empty_description():
    """Test that rows with empty description are skipped."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "5.25",
        ],
        [
            "Interest",
            "Data",
            "USD",
            "2024-01-31",
            "",  # Empty description
            "8.50",
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].description != ""


# =============================================================================
# Error Condition Tests
# =============================================================================


def test_error_invalid_amount():
    """Test that invalid amount format raises ValueError."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "invalid",  # Invalid amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError):
        parse_interest(model)


def test_error_empty_amount():
    """Test that empty amount raises ValueError (to_dec_strict)."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "",  # Empty amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="empty string"):
        parse_interest(model)


def test_parse_amount_with_thousand_separators():
    """Test parsing interest amounts with comma thousand separators."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest for Jan-2024",
            "1,250.75",  # Comma separator
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].amount == Decimal("1250.75")


def test_parse_high_precision_amount():
    """Test parsing interest amounts with high decimal precision."""
    rows = [
        [
            "Interest",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Interest",
            "Data",
            "EUR",
            "2024-01-31",
            "EUR Credit Interest",
            "5.123456789",  # High precision
        ],
    ]

    model = _parse_rows(rows)
    interest = parse_interest(model)

    assert len(interest) == 1
    assert interest[0].amount == Decimal("5.123456789")
