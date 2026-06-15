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


def _reject_non_finite(value: Decimal, source: object) -> Decimal:
    """Reject a non-finite Decimal (NaN or Infinity), returning a finite one unchanged.

    Both construct as valid Decimals but are never valid IBKR figures, and each silently
    corrupts downstream money math: a NaN traps on every later ordering comparison, and
    an Infinity zeroes a converted figure via 1/Infinity. Raising InvalidOperation (the
    same failure a malformed string raises) lets each caller map it to its own
    disposition: default for to_dec, ValueError for to_dec_strict.
    """
    if not value.is_finite():
        raise InvalidOperation(f"non-finite numeric value: {source!r}")
    return value


def _coerce_or_text(s: str | float | int | Decimal | None) -> Decimal | str | None:
    """Resolve the numeric fast-paths shared by to_dec and to_dec_strict.

    Returns None for a None input, a finite Decimal when the value is already numeric (a
    Decimal passes through, an int/float widens), or the stripped text otherwise. A
    non-finite numeric input raises InvalidOperation here (see _reject_non_finite) so
    the numeric fast-path cannot slip a NaN/Infinity past the string-path guard in
    _parse_clean. A returned str still needs the IBKR number grammar; each caller layers
    its own empty, placeholder, and malformed policy on top.
    """
    if s is None:
        return None
    if isinstance(s, Decimal):
        return _reject_non_finite(s, s)
    if isinstance(s, (int, float)):
        return _reject_non_finite(Decimal(str(s)), s)
    return s.strip()


def _parse_clean(text: str) -> Decimal:
    """Apply IBKR number grammar: drop thousands separators, then parse.

    Raises InvalidOperation on malformed input, including a non-finite NaN/Infinity (see
    _reject_non_finite). The caller maps that to its own disposition (default for
    to_dec, ValueError for to_dec_strict).
    """
    return _reject_non_finite(Decimal(NUM_CLEAN_RE.sub("", text)), text)


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
    # InvalidOperation (a non-finite numeric input, or a malformed string from
    # _parse_clean) maps to the default, the same disposition as any unparseable cell.
    try:
        coerced = _coerce_or_text(s)
        if coerced is None:
            return default
        if isinstance(coerced, Decimal):
            return coerced
        if not coerced:
            return default

        # Silent placeholders
        if coerced in {"-", "--"}:
            return default

        # Warn on elided/missing data
        if coerced in {"...", "N/A", "n/a"}:
            logger.warning(
                'Encountered elided/unavailable value "%s"; treating as %s.',
                coerced,
                default,
            )
            return default

        return _parse_clean(coerced)
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
    # InvalidOperation is the only arithmetic failure here: a non-finite numeric input
    # (rejected in _coerce_or_text) or a malformed string (rejected in _parse_clean).
    # Map it to the domain ValueError. The missing/empty/placeholder ValueErrors raised
    # below are not InvalidOperation, so they pass through this handler untouched.
    try:
        coerced = _coerce_or_text(s)
        if coerced is None:
            raise ValueError("Value is None")
        if isinstance(coerced, Decimal):
            return coerced
        if not coerced:
            raise ValueError("Value is empty string")
        if coerced in ELISION_PLACEHOLDERS:
            raise ValueError(f"Value is a placeholder: {coerced!r}")
        return _parse_clean(coerced)
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
