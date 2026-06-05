from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal, InvalidOperation

NUM_CLEAN_RE = re.compile(r"[,\s]")  # remove thousands separators, spaces


logger = logging.getLogger(__name__)


def to_dec(
    s: str | float | int | Decimal | None, default: Decimal = Decimal("0")
) -> Decimal:
    """Convert IBKR numeric strings to Decimal safely, coercing placeholders to default.

    Handles:
    - None, "" -> default
    - "-", "--" -> default (common IBKR nulls)
    - "...", "N/A" -> default (with warning for elided data)
    - "1,234.56" -> Decimal("1234.56")
    """
    if s is None:
        return default
    if isinstance(s, Decimal):
        return s
    if isinstance(s, (int, float)):
        return Decimal(str(s))

    s_stripped = s.strip()
    if not s_stripped:
        return default

    # Silent placeholders
    if s_stripped in {"-", "--"}:
        return default

    # Warn on elided/missing data
    if s_stripped in {"...", "N/A", "n/a"}:
        logger.warning(
            'Encountered elided/unavailable value "%s"; treating as %s.',
            s_stripped,
            default,
        )
        return default

    try:
        s_clean = NUM_CLEAN_RE.sub("", s_stripped)
        return Decimal(s_clean)
    except InvalidOperation:
        # Log error but don't crash; return default
        logger.error("Failed to parse number from: %r; using %s", s, default)
        return default


def to_dec_strict(s: str | float | int | Decimal | None) -> Decimal:
    """Convert IBKR numeric strings to Decimal.

    Raises ValueError on invalid/missing data.
    Use this for critical fields (Quantity, Proceeds) where 0 is not safe.
    """
    if s is None:
        raise ValueError("Value is None")
    if isinstance(s, Decimal):
        return s
    if isinstance(s, (int, float)):
        return Decimal(str(s))

    s_stripped = s.strip()
    if not s_stripped:
        raise ValueError("Value is empty string")

    if s_stripped in {"-", "--", "...", "N/A", "n/a"}:
        raise ValueError(f"Value is a placeholder: {s_stripped!r}")

    try:
        s_clean = NUM_CLEAN_RE.sub("", s_stripped)
        return Decimal(s_clean)
    except InvalidOperation as e:
        raise ValueError(f"Invalid decimal format: {s!r}") from e


def parse_date(d: str) -> dt.date:
    """Parse date-like strings.
    Handles 'YYYY-MM-DD' or 'YYYY-MM-DD, HH:MM:SS' or 'YYYY-MM-DD, HH:MM' etc.
    """
    if "," in d:
        d = d.split(",")[0].strip()
    return dt.date.fromisoformat(d)


# IBKR renders the Statement 'Period' field with a full month name, e.g.
# "January 1, 2024" -- distinct from the ISO Date/Time of trade rows.
_PERIOD_DATE_FORMAT = "%B %d, %Y"
_PERIOD_SEPARATOR = " - "


def parse_statement_period(text: str) -> tuple[dt.date, dt.date]:
    """Parse an IBKR Statement 'Period' field into a closed [start, end] date interval.

    'January 1, 2024 - December 31, 2024' -> (date(2024, 1, 1), date(2024, 12, 31)).
    A single-day statement carries no separator, so start == end. Raises ValueError on
    an unparseable value (missing/extra separator, or a side that is not a month-name
    date), so a caller can treat an unverifiable period as a hard failure.
    """
    parts = text.split(_PERIOD_SEPARATOR)
    if len(parts) == 1:
        start_str = end_str = parts[0].strip()
    elif len(parts) == 2:
        start_str, end_str = parts[0].strip(), parts[1].strip()
    else:
        raise ValueError(f"Ambiguous statement period (multiple separators): {text!r}")

    try:
        start = dt.datetime.strptime(start_str, _PERIOD_DATE_FORMAT).date()
        end = dt.datetime.strptime(end_str, _PERIOD_DATE_FORMAT).date()
    except ValueError as e:
        raise ValueError(f"Unparseable statement period: {text!r}") from e

    if end < start:
        raise ValueError(f"Statement period ends before it starts: {text!r}")
    return start, end
