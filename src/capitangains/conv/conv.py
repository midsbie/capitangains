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


def date_key(d: str | dt.date) -> str:
    """Return YYYY-MM-DD string for a date."""
    if isinstance(d, dt.date):
        return d.isoformat()
    # If it's 'YYYY-MM-DD, 09:30:00', strip time
    if "," in d:
        d = d.split(",")[0].strip()
    return d
