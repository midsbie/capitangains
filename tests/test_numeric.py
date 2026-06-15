import logging
from decimal import Decimal

import pytest

from capitangains.conv.numeric import to_decimal, to_decimal_strict


def test_to_decimal_strict_parses_sign_and_whitespace():
    assert to_decimal_strict("100") == Decimal("100")
    assert to_decimal_strict(" 100 ") == Decimal("100")
    assert to_decimal_strict("\t100\n") == Decimal("100")
    assert to_decimal_strict("-100.50") == Decimal("-100.50")


def test_to_decimal_strict_parses_scientific_notation():
    assert to_decimal_strict("1.5E2") == Decimal("150")
    assert to_decimal_strict("1E-2") == Decimal("0.01")


def test_to_decimal_strict_raises_on_missing_and_empty():
    with pytest.raises(ValueError, match="Value is None"):
        to_decimal_strict(None)
    with pytest.raises(ValueError, match="Value is empty string"):
        to_decimal_strict("")
    with pytest.raises(ValueError, match="Value is empty string"):
        to_decimal_strict("   ")


def test_to_decimal_defaults_on_missing_and_malformed(caplog):
    assert to_decimal(None) == Decimal("0")
    assert to_decimal("") == Decimal("0")
    assert to_decimal("   ") == Decimal("0")
    assert to_decimal(None, default=Decimal("-1")) == Decimal("-1")
    with caplog.at_level(logging.ERROR):
        assert to_decimal("invalid") == Decimal("0")
    assert "Failed to parse number" in caplog.text


def test_generic_parser_is_source_agnostic():
    # The generic core speaks no cell grammar: a thousands-grouped number and an IBKR
    # placeholder are both malformed here, neither cleaned nor honored. Those meanings
    # are added only by the conv.ibkr layer; keeping them out of numeric is the whole
    # point of the split (operator input must not inherit IBKR's locale by accident).
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_decimal_strict("1,234.56")
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_decimal_strict("--")
    assert to_decimal("1,234.56", default=Decimal("-1")) == Decimal("-1")


def test_to_decimal_strict_rejects_non_finite():
    # NaN/Infinity are valid Decimal constructions but never valid figures: a NaN traps
    # on later comparisons and an Infinity zeroes a converted value via 1/Infinity, so
    # the strict path must refuse them as malformed. This holds on every input axis (a
    # string token, a float, an already-built Decimal), so the numeric fast-path cannot
    # slip a non-finite value past the string guard.
    non_finite = (
        "NaN",
        "Infinity",
        "inf",
        "-inf",
        "-Infinity",
        float("inf"),
        float("nan"),
        Decimal("NaN"),
        Decimal("-Infinity"),
    )
    for value in non_finite:
        with pytest.raises(ValueError, match="Invalid decimal format"):
            to_decimal_strict(value)


def test_to_decimal_non_finite_uses_default(caplog):
    # The lenient path treats a non-finite value as malformed too, on every input axis
    # (string, float, Decimal): log an error and fall back to the default instead of
    # returning a NaN/Infinity that silently corrupts downstream sums.
    non_finite = (
        "Infinity",
        "NaN",
        float("inf"),
        float("nan"),
        Decimal("Infinity"),
        Decimal("NaN"),
    )
    with caplog.at_level(logging.ERROR):
        for value in non_finite:
            assert to_decimal(value) == Decimal("0")
    assert "Failed to parse number" in caplog.text
