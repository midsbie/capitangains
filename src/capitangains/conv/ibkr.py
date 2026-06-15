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


def _split_datetime(d: str) -> tuple[str, str]:
    """Split an IBKR Date/Time on its separating comma into stripped (date, time) parts.

    The comma is IBKR's date/time separator: 'YYYY-MM-DD' has none, 'YYYY-MM-DD,
    HH:MM:SS' has one. Either part may be empty (no comma at all, or a bare
    'YYYY-MM-DD,' trailer with nothing after it). The single home for that separator
    fact, so has_intraday_time and parse_date read one split rather than each
    rediscovering the comma and diverging on it.
    """
    date_part, _, time_part = d.partition(",")
    return date_part.strip(), time_part.strip()


def has_intraday_time(d: str) -> bool:
    """Whether an IBKR Date/Time string carries an orderable intraday time.

    IBKR renders a date-only value as 'YYYY-MM-DD' and a timestamped one as 'YYYY-MM-DD,
    HH:MM:SS' (or '..., HH:MM'). True only when the comma is followed by a parseable
    time, not merely present: a bare or whitespace-only trailer ('YYYY-MM-DD,') carries
    no order. The ordering-collision gate counts a row this returns False for as
    untimed, so a missing or malformed time fails closed (the run aborts) rather than
    sorting on an empty time and fabricating a FIFO order the data does not determine.

    """
    _, time_part = _split_datetime(d)
    try:
        dt.time.fromisoformat(time_part)
    except ValueError:
        return False
    return True


def parse_date(d: str) -> dt.date:
    """Parse the date from an IBKR Date/Time string, ignoring any intraday time.

    Handles 'YYYY-MM-DD', 'YYYY-MM-DD, HH:MM:SS', 'YYYY-MM-DD, HH:MM', and a bare
    'YYYY-MM-DD,' trailer alike. Raises ValueError on a missing or malformed date, which
    the extract layer turns into a row defect rather than a crash (see _require_date).
    """
    date_part, _ = _split_datetime(d)
    return dt.date.fromisoformat(date_part)
