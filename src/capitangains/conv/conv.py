from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal, InvalidOperation

# Strip thousands separators and whitespace before Decimal parsing. Safe ONLY for IBKR
# statement numbers, where a comma is a thousands separator, never a decimal comma
# (even when IBKR groups inconsistently within a row). Operator-supplied numbers (the
# --fx-table CSV) carry no such guarantee, so they must NOT pass through this cleaner.
# See fx._parse_fx_rate.
NUM_CLEAN_RE = re.compile(r"[,\s]")

# The strings IBKR uses for an absent or elided numeric cell. One home for "what counts
# as elision", shared by to_dec_strict and _optional_decimal (extract._common).
ELISION_PLACEHOLDERS = frozenset({"-", "--", "...", "N/A", "n/a"})


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
        logger.error("Failed to parse number from: %r; using %s", s, default)
        return default


def to_dec_strict(s: str | float | int | Decimal | None) -> Decimal:
    """Convert an IBKR numeric string to Decimal, raising on invalid/missing data.

    "Strict" is the missing-value policy, not the number grammar: unlike to_dec this
    refuses to default a blank/placeholder/malformed cell to 0, raising ValueError
    instead. Use it for critical fields (Quantity, Proceeds) where 0 is not safe. It
    still assumes IBKR grammar (a comma is a thousands separator; see NUM_CLEAN_RE), so
    it is not suitable for operator-supplied numbers of unknown locale; the --fx-table
    rate is parsed strictly on both axes by fx._parse_fx_rate.
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

    if s_stripped in ELISION_PLACEHOLDERS:
        raise ValueError(f"Value is a placeholder: {s_stripped!r}")

    try:
        s_clean = NUM_CLEAN_RE.sub("", s_stripped)
        return Decimal(s_clean)
    except InvalidOperation as e:
        raise ValueError(f"Invalid decimal format: {s!r}") from e


def has_intraday_time(d: str) -> bool:
    """Whether an IBKR Date/Time string carries a time after its date.

    IBKR renders a date-only value as 'YYYY-MM-DD' and a timestamped one as
    'YYYY-MM-DD, HH:MM:SS' (or '..., HH:MM'). The comma is the date/time separator, so
    its presence is exactly the presence of an intraday time. This is the single home
    for that format fact; parse_date and the ordering-collision detector defer here.
    """
    return "," in d


def parse_date(d: str) -> dt.date:
    """Parse date-like strings.
    Handles 'YYYY-MM-DD' or 'YYYY-MM-DD, HH:MM:SS' or 'YYYY-MM-DD, HH:MM' etc.
    """
    if has_intraday_time(d):
        d = d.split(",")[0].strip()
    return dt.date.fromisoformat(d)
