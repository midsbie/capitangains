"""Generic, source-agnostic Decimal coercion.

The shared mechanics behind every number parse: coerce a value to Decimal, reject a
non-finite result, and decide the missing/malformed disposition. This layer knows no
thousands separators and no placeholder vocabulary, so it imposes no locale of its own;
a caller that speaks a specific cell grammar (see conv.ibkr) cleans the string first and
delegates here. Keeping the genuinely generic core separate is what lets operator input
(the --fx-table CSV) parse without inheriting IBKR's statement locale by accident.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


def _reject_non_finite(value: Decimal, source: object) -> Decimal:
    """Reject a non-finite Decimal (NaN or Infinity), returning a finite one unchanged.

    Both construct as valid Decimals but are never valid figures, and each silently
    corrupts downstream money math: a NaN traps on every later ordering comparison, and
    an Infinity zeroes a converted figure via 1/Infinity. Raising InvalidOperation (the
    same failure a malformed string raises) lets each caller map it to its own
    disposition: default for to_decimal, ValueError for to_decimal_strict.
    """
    if not value.is_finite():
        raise InvalidOperation(f"non-finite numeric value: {source!r}")
    return value


def _coerce_or_text(value: str | float | int | Decimal | None) -> Decimal | str | None:
    """Resolve the numeric fast-paths shared by to_decimal and to_decimal_strict.

    Returns None for a None input, a finite Decimal when the value is already numeric (a
    Decimal passes through, an int/float widens), or the stripped text otherwise. A
    non-finite numeric input raises InvalidOperation here (see _reject_non_finite) so
    the numeric fast-path cannot slip a NaN/Infinity past the string-path guard in
    to_decimal/to_decimal_strict. A returned str still needs parsing; each caller layers
    its own empty and malformed policy on top.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _reject_non_finite(value, value)
    if isinstance(value, (int, float)):
        return _reject_non_finite(Decimal(str(value)), value)
    return value.strip()


def to_decimal(
    value: str | float | int | Decimal | None, default: Decimal = Decimal("0")
) -> Decimal:
    """Coerce a value to Decimal, falling back to default on absent or malformed input.

    Source-agnostic: a grouped "1,234.56" or a sentinel like "--" is malformed here, not
    cleaned or honored (that grammar lives in conv.ibkr). None, an empty/whitespace
    string, a malformed string, and a non-finite numeric all map to default.
    """
    # A non-finite numeric (from _coerce_or_text) or a malformed string (from the parse
    # below) raises InvalidOperation; map it to the default, like any unparseable input.
    try:
        coerced = _coerce_or_text(value)
        if coerced is None:
            return default
        if isinstance(coerced, Decimal):
            return coerced
        if not coerced:
            return default
        return _reject_non_finite(Decimal(coerced), coerced)
    except InvalidOperation:
        logger.error("Failed to parse number from: %r; using %s", value, default)
        return default


def to_decimal_strict(value: str | float | int | Decimal | None) -> Decimal:
    """Coerce a value to Decimal, raising ValueError on absent or malformed input.

    The strict counterpart of to_decimal: where that defaults, this raises, so a
    critical field never silently becomes 0. Equally source-agnostic (no separator
    stripping, no placeholder vocabulary): None raises "Value is None", an
    empty/whitespace string "Value is empty string", and a malformed string or
    non-finite numeric "Invalid decimal format".
    """
    # InvalidOperation is the only arithmetic failure caught here: a non-finite numeric
    # (rejected in _coerce_or_text) or a malformed string (from the parse below). The
    # None/empty ValueErrors raised in the try are not InvalidOperation, so they pass
    # through this handler untouched.
    try:
        coerced = _coerce_or_text(value)
        if coerced is None:
            raise ValueError("Value is None")
        if isinstance(coerced, Decimal):
            return coerced
        if not coerced:
            raise ValueError("Value is empty string")
        return _reject_non_finite(Decimal(coerced), coerced)
    except InvalidOperation as e:
        raise ValueError(f"Invalid decimal format: {value!r}") from e
