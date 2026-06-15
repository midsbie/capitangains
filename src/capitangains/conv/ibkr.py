"""IBKR statement-cell grammar over the generic numeric core.

A thin layer that adds what an IBKR Activity Statement assumes but the generic parser
does not: a fixed thousands-separator convention, a placeholder vocabulary for absent
cells, and the Date/Time format. It strips/recognizes those, then delegates the actual
coercion to conv.numeric (no number parsing is reimplemented here). Safe only for IBKR
statement cells; operator-supplied numbers carry no such locale (see fx._parse_fx_rate).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal

from .numeric import to_decimal, to_decimal_strict

logger = logging.getLogger(__name__)

# Strip thousands separators and whitespace before Decimal parsing. Safe ONLY for IBKR
# statement numbers, where a comma is a thousands separator, never a decimal comma
# (even when IBKR groups inconsistently within a row). Operator-supplied numbers (the
# --fx-table CSV) carry no such guarantee, so they must NOT pass through this cleaner.
# See fx._parse_fx_rate.
NUM_CLEAN_RE = re.compile(r"[,\s]")

# The strings IBKR uses for an absent or elided numeric cell, split by disposition: the
# silent nulls are routine empties, the warn nulls signal elided data worth a log line.
# Their union is the public set _optional_decimal (extract._common) tests against; one
# home for "what counts as elision", defined once rather than relisted inline per
# branch.
_SILENT_NULLS = frozenset({"-", "--"})
_WARN_NULLS = frozenset({"...", "N/A", "n/a"})
ELISION_PLACEHOLDERS = _SILENT_NULLS | _WARN_NULLS


def to_dec_strict(value: str | float | int | Decimal | None) -> Decimal:
    """Parse an IBKR numeric cell to Decimal, raising on invalid/missing data.

    "Strict" is the missing-value policy, not the number grammar: a blank, placeholder,
    or malformed cell raises ValueError rather than defaulting to 0. Use it for critical
    fields (Quantity, Proceeds) where 0 is not safe. A placeholder is rejected by name
    and thousands separators are stripped before the generic strict parser handles sign,
    decimal point, non-finite, and the empty/malformed cases. IBKR grammar treats a
    comma as a thousands separator, so this is not suitable for operator-supplied
    numbers of unknown locale; the --fx-table rate is parsed by fx._parse_fx_rate
    instead.
    """
    # Branch on str once: cell grammar applies only to strings; a non-str input skips it
    # and delegates straight to numeric, so coercion never runs twice.
    if isinstance(value, str):
        t = value.strip()
        if t in ELISION_PLACEHOLDERS:
            raise ValueError(f"Value is a placeholder: {t!r}")
        value = NUM_CLEAN_RE.sub("", t)
    return to_decimal_strict(value)


def to_dec(
    value: str | float | int | Decimal | None, default: Decimal = Decimal("0")
) -> Decimal:
    """Parse an IBKR numeric cell to Decimal, coercing placeholders to default.

    Handles:
    - None, "" -> default
    - "-", "--" -> default (common IBKR nulls)
    - "...", "N/A", "n/a" -> default (with a warning for elided data)
    - "1,234.56" -> Decimal("1234.56")
    """
    if isinstance(value, str):
        t = value.strip()
        if t in _SILENT_NULLS:
            return default
        if t in _WARN_NULLS:
            logger.warning(
                'Encountered elided/unavailable value "%s"; treating as %s.', t, default
            )
            return default
        value = NUM_CLEAN_RE.sub("", t)
    return to_decimal(value, default)


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
