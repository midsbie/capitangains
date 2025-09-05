from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from capitangains.logging import configure_logging

NUM_CLEAN_RE = re.compile(r"[,\s]")  # remove thousands separators, spaces


logger = configure_logging()


def to_dec(s: str | float | int | Decimal | None) -> Decimal:
    """Convert IBKR numeric strings to Decimal safely.
    Accepts "1,234.56", "-101.93155704", "", None -> Decimal(0) if empty.
    """
    if s is None:
        return Decimal("0")
    if isinstance(s, Decimal):
        return s
    if isinstance(s, (int, float)):
        return Decimal(str(s))
    s = s.strip()
    if not s:
        return Decimal("0")
    # Some IBKR CSVs can contain "..." in sanitized exports. Treat as zero but warn.
    if "..." in s:
        logger.warning(
            'Encountered elided numeric value "%s"; treating as 0 for safety.', s
        )
        return Decimal("0")
    try:
        s_clean = NUM_CLEAN_RE.sub("", s)
        return Decimal(s_clean)
    except InvalidOperation:
        logger.exception(f"Failed to parse number from: {s!r}")
        return Decimal("0")


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
