"""StatementPeriod / StatementMetadata value objects and the metadata extractor.

- StatementPeriod: a closed [start, end] interval, valid by construction, owning the
  month-name 'Period' parse and the overlap test.
- parse_statement_metadata: reads the account number and reporting period out of a
  parsed model, raising DataQualityError naming the first missing/malformed field.
"""

import datetime as dt

import pytest

from capitangains.errors import DataQualityError
from capitangains.reporting.extract import (
    StatementMetadata,
    StatementPeriod,
    parse_statement_metadata,
)
from tests.support import parse_model

# --- StatementPeriod.parse ------------------------------------------------------------


def test_parse_full_year():
    assert StatementPeriod.parse("January 1, 2024 - December 31, 2024") == (
        StatementPeriod(dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    )


def test_parse_single_day_has_no_separator():
    # A one-day statement carries just the date; start == end.
    assert StatementPeriod.parse("March 15, 2023") == (
        StatementPeriod(dt.date(2023, 3, 15), dt.date(2023, 3, 15))
    )


def test_parse_rejects_unparseable_side():
    with pytest.raises(ValueError, match="Unparseable statement period"):
        StatementPeriod.parse("January 1, 2024 - 2024-12-31")


def test_parse_rejects_iso_format():
    # The Period field is month-name, never ISO; an ISO value is a wrong-field defect.
    with pytest.raises(ValueError, match="Unparseable statement period"):
        StatementPeriod.parse("2024-01-01")


def test_parse_rejects_extra_separator():
    with pytest.raises(ValueError, match="Ambiguous statement period"):
        StatementPeriod.parse("January 1, 2024 - June 1, 2024 - December 31, 2024")


def test_parse_rejects_reversed_range():
    with pytest.raises(ValueError, match="ends before it starts"):
        StatementPeriod.parse("December 31, 2024 - January 1, 2024")


# --- StatementPeriod invariant + overlap ----------------------------------------------


def test_construction_rejects_inverted_interval():
    # The invariant is structural: no code path can hold an end-before-start period,
    # whether built from the CSV or directly from two dates.
    with pytest.raises(ValueError, match="ends before it starts"):
        StatementPeriod(dt.date(2024, 12, 31), dt.date(2024, 1, 1))


def _period(start, end):
    return StatementPeriod(dt.date(*start), dt.date(*end))


def test_overlaps_disjoint_is_false():
    assert not _period((2023, 1, 1), (2023, 12, 31)).overlaps(
        _period((2024, 1, 1), (2024, 12, 31))
    )


def test_overlaps_shared_boundary_day_is_true():
    # Closed intervals: touching on a single day counts as overlap.
    a = _period((2024, 1, 1), (2024, 6, 30))
    b = _period((2024, 6, 30), (2024, 12, 31))
    assert a.overlaps(b) and b.overlaps(a)


def test_overlaps_containment_and_symmetry():
    outer = _period((2024, 1, 1), (2024, 12, 31))
    inner = _period((2024, 6, 1), (2024, 6, 1))  # single day inside
    assert outer.overlaps(inner) and inner.overlaps(outer)


# --- parse_statement_metadata ---------------------------------------------------------


_STATEMENT_HEADER = ["Statement", "Header", "Field Name", "Field Value"]
_ACCOUNT_HEADER = ["Account Information", "Header", "Field Name", "Field Value"]


def test_extracts_account_and_period():
    model = parse_model(
        [
            _STATEMENT_HEADER,
            ["Statement", "Data", "Title", "Activity Statement"],
            ["Statement", "Data", "Period", "January 1, 2024 - December 31, 2024"],
            _ACCOUNT_HEADER,
            ["Account Information", "Data", "Name", "Jane Doe"],
            ["Account Information", "Data", "Account", "U1234567"],
        ]
    )
    assert parse_statement_metadata(model) == StatementMetadata(
        account="U1234567",
        period=StatementPeriod(dt.date(2024, 1, 1), dt.date(2024, 12, 31)),
    )


def test_missing_account_raises():
    model = parse_model(
        [
            _STATEMENT_HEADER,
            ["Statement", "Data", "Period", "January 1, 2024 - December 31, 2024"],
        ]
    )
    with pytest.raises(DataQualityError, match="missing account number"):
        parse_statement_metadata(model)


def test_missing_period_raises():
    model = parse_model(
        [
            _ACCOUNT_HEADER,
            ["Account Information", "Data", "Account", "U1234567"],
        ]
    )
    with pytest.raises(DataQualityError, match="missing reporting period"):
        parse_statement_metadata(model)


def test_malformed_period_raises():
    model = parse_model(
        [
            _ACCOUNT_HEADER,
            ["Account Information", "Data", "Account", "U1234567"],
            _STATEMENT_HEADER,
            ["Statement", "Data", "Period", "2024-01-01 to 2024-12-31"],
        ]
    )
    with pytest.raises(DataQualityError, match="statement period"):
        parse_statement_metadata(model)
