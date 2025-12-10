import logging
from decimal import Decimal

import pytest

from capitangains.conv import to_dec, to_dec_strict


def test_to_dec_standard():
    assert to_dec("123") == Decimal("123")
    assert to_dec("123.45") == Decimal("123.45")
    assert to_dec("1,234.56") == Decimal("1234.56")
    assert to_dec(100) == Decimal("100")
    assert to_dec(10.5) == Decimal("10.5")
    assert to_dec(Decimal("5.5")) == Decimal("5.5")


def test_to_dec_placeholders_silent():
    # These should return default (0) without logging warning
    assert to_dec(None) == Decimal("0")
    assert to_dec("") == Decimal("0")
    assert to_dec("   ") == Decimal("0")
    assert to_dec("-") == Decimal("0")
    assert to_dec("--") == Decimal("0")

    # Check alias
    assert to_dec("--") == Decimal("0")


def test_to_dec_placeholders_warn(caplog):
    # These should return default (0) AND warn
    with caplog.at_level(logging.WARNING):
        assert to_dec("...") == Decimal("0")
        assert "Encountered elided/unavailable value" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert to_dec("N/A") == Decimal("0")
        assert "Encountered elided/unavailable value" in caplog.text


def test_to_dec_invalid_format(caplog):
    # These should return default (0) AND log error
    with caplog.at_level(logging.ERROR):
        assert to_dec("invalid") == Decimal("0")
        assert "Failed to parse number" in caplog.text


def test_to_dec_custom_default():
    assert to_dec(None, default=Decimal("-1")) == Decimal("-1")
    assert to_dec("--", default=Decimal("999")) == Decimal("999")


def test_to_dec_strict_standard():
    assert to_dec_strict("100") == Decimal("100")
    assert to_dec_strict("1,000.00") == Decimal("1000.00")
    assert to_dec_strict(10) == Decimal("10")


def test_to_dec_strict_raises():
    with pytest.raises(ValueError, match="Value is None"):
        to_dec_strict(None)

    with pytest.raises(ValueError, match="Value is empty string"):
        to_dec_strict("")

    with pytest.raises(ValueError, match="Value is a placeholder"):
        to_dec_strict("--")

    with pytest.raises(ValueError, match="Value is a placeholder"):
        to_dec_strict("...")

    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_dec_strict("abc")


def test_to_dec_strict_edge_cases():
    # Whitespace handling
    assert to_dec_strict(" 100 ") == Decimal("100")
    assert to_dec_strict("\t100\n") == Decimal("100")

    # Negative numbers
    assert to_dec_strict("-100.50") == Decimal("-100.50")

    # Thousand separators
    assert to_dec_strict("1,234,567.89") == Decimal("1234567.89")

    # Currency symbols (Strict parser should reject these as invalid format)
    # IBKR CSVs separate currency into its own column.
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_dec_strict("$100.00")
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_dec_strict("€100.00")

    # Case sensitivity for placeholders
    with pytest.raises(ValueError, match="Value is a placeholder"):
        to_dec_strict("n/a")
    with pytest.raises(ValueError, match="Value is a placeholder"):
        to_dec_strict("N/A")


def test_to_dec_strict_scientific_notation():
    # Decimal supports scientific notation, verify it passes
    assert to_dec_strict("1.5E2") == Decimal("150")
    assert to_dec_strict("1E-2") == Decimal("0.01")
