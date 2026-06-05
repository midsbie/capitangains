"""Shared extraction helpers and the row-level defect record.

Package-internal: the outside world imports the public ``ExtractionDefect`` via the
``extract`` package root, not from here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from capitangains.conv import parse_date, to_dec_strict
from capitangains.errors import DataQualityError


def _is_total_or_empty(value: str) -> bool:
    """Return True if value is empty or a 'Total' summary row."""
    return not value or value.lower().startswith("total")


def _is_data_row(currency: str, date: str, description: str) -> bool:
    """Return True if a cash-flow row carries the core fields of a data line.

    A row is a data line only when currency, date, and description are all present;
    rows failing this are typically Total/summary or otherwise non-data lines. Genuine
    structural anomalies are already surfaced by IbkrStatementCsvParser, so callers skip
    these quietly (aggregating a count) rather than re-logging per row.
    """
    return bool(currency and date and description)


def _require_fields(label: str, **fields: str) -> None:
    """Raise DataQualityError naming every empty required field."""
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise DataQualityError(f"Invalid {label}: missing {', '.join(missing)}")


def _require_decimal(label: str, field: str, value: str) -> Decimal:
    """Strictly parse a required numeric field, reporting failures as data errors.

    Wraps ``to_dec_strict`` so a missing/placeholder/malformed value surfaces as a
    structured ``DataQualityError`` (exit 2 at the CLI boundary) instead of an
    uncaught ``ValueError`` traceback. The original cause text is preserved.
    """
    try:
        return to_dec_strict(value)
    except ValueError as e:
        raise DataQualityError(f"Invalid {label}: bad {field} {value!r} ({e})") from e


def _require_date(label: str, field: str, value: str) -> dt.date:
    """Parse a required date field, reporting failures as data errors (see above)."""
    try:
        return parse_date(value)
    except ValueError as e:
        raise DataQualityError(f"Invalid {label}: bad {field} {value!r} ({e})") from e


@dataclass(frozen=True)
class ExtractionDefect:
    """One rejected extraction row, accumulated for a single boundary report.

    The extractors collect these instead of raising on the first bad row, so the
    operator sees every defect in one pass (the CLI logs each, then exits 2 -- mirroring
    the FX and gap-acknowledgment reports). Identity is the semantic locator (section,
    symbol, date), read from the row dict at the catch site so it survives even when one
    of those fields is itself the malformed value. ``reason`` is the DataQualityError
    text, which already names the first offending field and its bad value; the catch is
    per-row, so no separate field locator is needed.
    """

    section: str
    symbol: str | None
    date: str | None
    reason: str
