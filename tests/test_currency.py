"""Unit tests for the Currency value object and the case-unification it closes."""

from capitangains.conv import EUR, Currency
from tests.support import buy, ingest, make_matcher, sell


def test_normalizes_case_and_whitespace_on_construction():
    assert Currency("usd").code == "USD"
    assert Currency("  eur ").code == "EUR"
    assert Currency("Gbp").code == "GBP"


def test_codes_differing_only_in_case_are_one_value():
    assert Currency("usd") == Currency("USD")
    assert hash(Currency("usd")) == hash(Currency("USD"))
    # The whole point of the type: it collapses to a single dict/set key.
    assert len({Currency("usd"), Currency("USD")}) == 1
    assert {Currency("usd"): 1}[Currency("USD")] == 1


def test_not_equal_to_a_bare_string():
    # A Currency is its own type, never the raw code string. This is what keeps a
    # raw-string key from silently colliding with (or masquerading as) a Currency key.
    assert Currency("USD") != "USD"


def test_is_base_recognizes_eur_case_insensitively():
    assert Currency("EUR").is_base
    assert Currency("eur").is_base
    assert EUR.is_base
    assert not Currency("USD").is_base


def test_str_is_the_code():
    assert str(Currency("usd")) == "USD"


def test_orders_by_code():
    assert sorted([Currency("USD"), Currency("EUR"), Currency("GBP")]) == [
        Currency("EUR"),
        Currency("GBP"),
        Currency("USD"),
    ]


def test_mixed_case_currency_unifies_in_fifo_matching():
    # Regression: a buy stamped 'usd' and a sell stamped 'USD' are the same
    # instrument. Normalizing currency into the position-book key makes them one key, so
    # the sell consumes the buy lot rather than recording a phantom gap (the pre-fix
    # behavior, where the raw strings keyed two separate buckets).
    matcher = make_matcher()
    [line] = ingest(
        matcher,
        [
            buy("ABC", "2024-01-01", "10", "-1000", "-1", ccy="usd"),
            sell("ABC", "2024-02-01", "-10", "1200", "-1", ccy="USD"),
        ],
    )
    assert line.has_gap is False
    assert line.currency == Currency("USD")
