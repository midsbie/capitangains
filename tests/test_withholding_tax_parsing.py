"""Comprehensive unit tests for IBKR Withholding Tax parsing.

Test coverage for src/capitangains/reporting/extract.py::parse_withholding_tax
"""

import datetime as dt
from decimal import Decimal

import pytest

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import WithholdingRow, parse_withholding_tax


def _parse_rows(rows):
    """Helper to parse CSV rows into IbkrModel."""
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


# =============================================================================
# Happy Path Tests
# =============================================================================


def test_parse_dividend_withholding_with_country():
    """Test parsing dividend withholding tax with country code."""
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
            "2024-01-15",
            "AAPL (US1234567890) Cash Dividend USD 0.24 per Share - US Tax",
            "-7.20",
            "",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    w = withholding[0]
    assert w.currency == "USD"
    assert w.date == dt.date(2024, 1, 15)
    assert "Cash Dividend" in w.description
    assert w.amount == Decimal("-7.20")
    assert w.code == ""
    assert w.type == "Dividend"
    assert w.country == "US"


def test_parse_interest_withholding_with_country():
    """Test parsing interest withholding tax with country code."""
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
            "EUR",
            "2024-02-20",
            "Credit Interest - NL Tax",
            "-15.00",
            "W",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    w = withholding[0]
    assert w.currency == "EUR"
    assert w.type == "Interest"
    assert w.country == "NL"
    assert w.code == "W"


def test_parse_generic_dividend_withholding():
    """Test parsing withholding with 'dividend' keyword but no specific type."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-03-10",
            "Some dividend distribution - US Tax",
            "-5.50",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    w = withholding[0]
    assert w.type == "Dividend"  # Falls through to default dividend check
    assert w.country == "US"


def test_parse_with_code_field_present():
    """Test parsing when optional Code field is present."""
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
            "2024-05-15",
            "AAPL Cash Dividend - US Tax",
            "-10.00",
            "W",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].code == "W"


# =============================================================================
# Type Classification Tests
# =============================================================================


def test_type_classification_credit_interest():
    """Test type = 'Interest' for 'credit interest' keyword."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "EUR",
            "2024-01-15",
            "Credit Interest for January - NL Tax",
            "-5.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].type == "Interest"


def test_type_classification_interest_without_dividend():
    """Test type = 'Interest' for 'interest' without 'dividend'."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-02-20",
            "Interest Payment - US Tax",
            "-8.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].type == "Interest"


def test_type_classification_cash_dividend():
    """Test type = 'Dividend' for 'cash dividend'."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-03-10",
            "MSFT Cash Dividend USD 0.75 per Share - US Tax",
            "-22.50",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].type == "Dividend"


def test_type_classification_payment_in_lieu():
    """Test type = 'Dividend' for 'payment in lieu of dividend'."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-04-15",
            "Payment in Lieu of Dividend - US Tax",
            "-15.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].type == "Dividend"


def test_type_classification_empty_for_unrecognized():
    """Test type = '' (empty) for unrecognized description without 'dividend'."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-05-01",
            "Some Other Tax - US Tax",
            "-10.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].type == ""  # No recognized keywords


# =============================================================================
# Country Extraction Tests
# =============================================================================


def test_country_extraction_us():
    """Test extracting 'US' from ' - US Tax' pattern."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-5.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].country == "US"


def test_country_extraction_nl():
    """Test extracting 'NL' from ' - NL Tax' pattern."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "EUR",
            "2024-02-20",
            "Credit Interest - NL Tax",
            "-12.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].country == "NL"


def test_country_extraction_no_match():
    """Test that country is empty when pattern doesn't match."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-03-10",
            "Some withholding without country code",
            "-8.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].country == ""


# =============================================================================
# Filtering and Edge Cases
# =============================================================================


def test_skip_rows_with_empty_currency():
    """Test that rows with empty currency are skipped (total rows)."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-5.00",
        ],
        [
            "Withholding Tax",
            "Data",
            "",  # Empty currency (total row)
            "",
            "Total",
            "-5.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    # Only the first row should be parsed
    assert len(withholding) == 1
    assert withholding[0].currency == "USD"


def test_skip_rows_with_empty_date():
    """Test that rows with empty date are skipped."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-5.00",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "",  # Empty date
            "Some Description",
            "-10.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].date == dt.date(2024, 1, 15)


def test_skip_rows_with_empty_description():
    """Test that rows with empty description are skipped."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-5.00",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-16",
            "",  # Empty description
            "-8.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].description != ""


def test_parse_without_code_column():
    """Test that parsing works when Code column is absent."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-5.00",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].code == ""  # Defaults to empty string


def test_parse_amounts_with_thousand_separators():
    """Test parsing amounts with comma thousand separators."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "Large Dividend - US Tax",
            "-1,250.50",  # Comma separator
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 1
    assert withholding[0].amount == Decimal("-1250.50")


def test_parse_multiple_withholding_entries():
    """Test parsing multiple withholding tax entries."""
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
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "-7.20",
            "",
        ],
        [
            "Withholding Tax",
            "Data",
            "EUR",
            "2024-02-20",
            "Credit Interest - NL Tax",
            "-15.00",
            "W",
        ],
        [
            "Withholding Tax",
            "Data",
            "GBP",
            "2024-03-10",
            "BP Cash Dividend - GB Tax",
            "-5.50",
            "",
        ],
    ]

    model = _parse_rows(rows)
    withholding = parse_withholding_tax(model)

    assert len(withholding) == 3
    assert withholding[0].currency == "USD"
    assert withholding[1].currency == "EUR"
    assert withholding[2].currency == "GBP"


# =============================================================================
# Error Condition Tests
# =============================================================================


def test_error_invalid_amount():
    """Test that invalid amount format raises ValueError."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "invalid",  # Invalid amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError):
        parse_withholding_tax(model)


def test_error_empty_amount():
    """Test that empty amount raises ValueError (to_dec_strict)."""
    rows = [
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-15",
            "AAPL Cash Dividend - US Tax",
            "",  # Empty amount
        ],
    ]

    model = _parse_rows(rows)
    with pytest.raises(ValueError, match="empty string"):
        parse_withholding_tax(model)
