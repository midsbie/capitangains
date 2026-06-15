import logging
from decimal import Decimal

import pytest

from capitangains.conv.ibkr import to_dec, to_dec_strict


def test_strips_ibkr_thousands_separators():
    # A comma is an IBKR thousands separator; the generic core does not strip it, so
    # cleaning is the ibkr layer's job (lenient and strict alike).
    assert to_dec("1,234.56") == Decimal("1234.56")
    assert to_dec_strict("1,000.00") == Decimal("1000.00")
    assert to_dec_strict("1,234,567.89") == Decimal("1234567.89")


def test_non_string_inputs_pass_through_to_generic_core():
    # A non-str skips cell grammar entirely (the isinstance(str) branch) and delegates,
    # so coercion runs only once, in numeric.
    assert to_dec(100) == Decimal("100")
    assert to_dec(Decimal("5.5")) == Decimal("5.5")
    assert to_dec_strict(10) == Decimal("10")


def test_silent_nulls_default_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        for null in (None, "", "   ", "-", "--"):
            assert to_dec(null) == Decimal("0")
    assert "Encountered elided/unavailable value" not in caplog.text


def test_warn_nulls_default_with_warning(caplog):
    for placeholder in ("...", "N/A", "n/a"):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert to_dec(placeholder) == Decimal("0")
        assert "Encountered elided/unavailable value" in caplog.text


def test_lenient_malformed_uses_default(caplog):
    with caplog.at_level(logging.ERROR):
        assert to_dec("invalid") == Decimal("0")
    assert "Failed to parse number" in caplog.text


def test_lenient_custom_default():
    assert to_dec(None, default=Decimal("-1")) == Decimal("-1")
    assert to_dec("--", default=Decimal("999")) == Decimal("999")


def test_strict_rejects_every_placeholder():
    for placeholder in ("-", "--", "...", "N/A", "n/a"):
        with pytest.raises(ValueError, match="Value is a placeholder"):
            to_dec_strict(placeholder)


def test_strict_rejects_currency_symbols():
    # IBKR keeps the currency in its own column, so a symbol-prefixed amount is
    # malformed: the cleaner strips only commas/whitespace, never a '$' or '€'.
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_dec_strict("$100.00")
    with pytest.raises(ValueError, match="Invalid decimal format"):
        to_dec_strict("€100.00")
