import datetime as dt
import logging
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.fifo_domain import RealizedLine
from capitangains.reporting.reconcile import reconcile_realized_against_ibkr
from tests.support import realized_line, trade_row


def _trade(
    symbol,
    currency,
    quantity,
    realized,
    *,
    category="Stocks",
    year=2024,
    month=6,
    day=1,
):
    """A minimal TradeRow carrying IBKR's per-trade `Realized P/L` (trade ccy).

    category is the IBKR asset class; it governs elision policy (only Forex omits
    Realized legitimately), so an elided sell defaults to the anomalous Stocks case.
    """
    return trade_row(
        symbol=symbol,
        currency=currency,
        asset_category=category,
        quantity=quantity,
        t_price="1",
        proceeds="0",
        comm_fee="0",
        code="",
        date=dt.date(year, month, day),
        datetime_str=f"{year}-{month:02d}-{day:02d}, 10:00:00",
        realized_pl_ccy=realized,
    )


def _line(symbol, ccy, realized, *, gap_fixed=False, elided=False, year=2024):
    """A minimal RealizedLine carrying only what the reconciler reads.

    The reconciler keys off symbol/currency, sums realized_pl_ccy, filters on
    sell_date.year, and partitions on gap_fixed/ibkr_realized_elided. realized_pl_ccy is
    a property (net minus allocated cost), so with no legs the cost is zero and sell_net
    carries the figure the reconciler sums. elided mirrors the source sell's missing
    IBKR Realized P/L (set at replay in production); keep it consistent with its _trade.
    """
    return realized_line(
        symbol=symbol,
        currency=ccy,
        sell_date=dt.date(year, 6, 1),
        legs=[],
        sell_net_ccy=realized,
        has_gap=gap_fixed,  # synthesis only happens on a gap; keep the pair consistent
        gap_fixed=gap_fixed,
        ibkr_realized_elided=elided,
    )


def test_realized_matches_ibkr_in_trade_currency():
    # IBKR per-trade realized sums to 22493.07342 USD; our FIFO total quantizes to the
    # cent. Same currency, so they agree within the per-sell rounding band.
    trades = [
        _trade("GOOGL", "USD", "450", "0"),  # opening buy, realized 0
        _trade("GOOGL", "USD", "-450", "22493.07342"),  # closing sell
    ]
    lines = [_line("GOOGL", "USD", "22493.07")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert (r.symbol, r.currency) == ("GOOGL", Currency("USD"))
    assert r.computed == Decimal("22493.07")
    assert r.ibkr == Decimal("22493.07342")
    assert r.is_match


def test_reconciles_realized_not_grand_total():
    """Regression: a partially-closed position must reconcile against
    realized P/L, never IBKR's grand Total (= realized + unrealized).

    AMD-like: realized 1202.17, unrealized -56.08, grand Total 1146.09. The old code
    read the rightmost `Total` column (1146.09) and produced a false mismatch.
    """
    trades = [
        _trade("AMD", "USD", "200", "0"),  # buy
        _trade("AMD", "USD", "-100", "1202.17137978"),  # partial close -> realized
    ]
    lines = [_line("AMD", "USD", "1202.17")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert r.ibkr == Decimal("1202.17137978")  # realized, not the 1146.09 grand Total
    assert r.is_match


def test_open_only_position_is_not_a_mismatch():
    """A purely-unrealized position (only a buy, no close) has no realized activity, so
    it must not be reconciled -- the old code compared its grand Total against zero."""
    trades = [_trade("AAL", "USD", "100", "0")]  # opening buy only
    lines: list[RealizedLine] = []  # no FIFO realized line: position still open

    report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert report.reconciled == []
    assert report.synthetic == []


def test_material_difference_is_flagged():
    # A missing buy lot zeroed our cost basis, inflating the gain far beyond rounding.
    trades = [_trade("INTC", "USD", "-100", "-38115.23")]
    lines = [_line("INTC", "USD", "0")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert not r.is_match
    assert r.diff == Decimal("38115.23")


def test_rounding_tolerance_scales_with_sell_count():
    # Five sells, each rounded to the cent on our side, accumulate up to 0.025 of
    # rounding. A flat 0.01 tolerance would false-positive; the scaled one must not.
    trades = [_trade("XYZ", "USD", "-10", "1.004", day=d) for d in range(1, 6)]
    lines = [_line("XYZ", "USD", "5.00")]  # IBKR exact 5.020 vs our 5.00

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert r.n_sells == 5
    assert r.diff == Decimal("0.020")
    assert r.tolerance == Decimal("0.030")  # (5 + 1) * 0.005
    assert r.is_match


def test_elided_ibkr_realized_skips_forex_symbol_quietly(caplog):
    # Forex legitimately carries no Realized P/L, so a fully-elided Forex symbol is the
    # expected skip: it lands in `incomplete` (logged at INFO), not `anomalous_elision`.
    trades = [
        _trade("EUR.USD", "USD", "100", "0", category="Forex"),
        _trade("EUR.USD", "USD", "-100", None, category="Forex"),  # realized elided
    ]
    lines = [_line("EUR.USD", "USD", "50.00", elided=True)]  # mirrors the elided sell

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.reconcile"):
        report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert report.reconciled == []  # IBKR total untrustworthy -> not reconciled
    assert report.incomplete == [("EUR.USD", Currency("USD"))]
    assert report.anomalous_elision == []  # Forex elision is expected, not anomalous
    assert any(
        "skipped" in rec.getMessage() and "EUR.USD" in rec.getMessage()
        for rec in caplog.records
    )


def test_elided_realized_on_non_forex_sell_is_anomalous(caplog):
    # A Stocks sell with no IBKR Realized P/L breaks the invariant (only Forex elides).
    # It is flagged `anomalous_elision`, kept OUT of the quiet `incomplete` skip, and
    # warned at the boundary, even though it still cannot be cross-checked.
    trades = [
        _trade("ABC", "USD", "100", "0"),  # Stocks buy
        _trade("ABC", "USD", "-100", None),  # Stocks sell, realized unexpectedly blank
    ]
    lines = [_line("ABC", "USD", "50.00", elided=True)]

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.reconcile"):
        report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert report.anomalous_elision == [("ABC", Currency("USD"))]
    assert report.incomplete == []  # not folded into the benign skip
    assert report.reconciled == []  # still nothing comparable
    # The anomaly is warned by the boundary reporter, not the detector: no INFO skip.
    assert not any("skipped" in rec.getMessage() for rec in caplog.records)


def test_sibling_elided_sell_does_not_drop_a_valid_sells_mismatch():
    # ABC/USD has two closing sells: one valid (IBKR realized 100.00) and one
    # whose IBKR realized is elided. Our FIFO is wrong on the *valid* sell (999.00 vs
    # 100.00), which is exactly the kind of error the cross-check exists to catch.
    # Eliding one trade makes only that trade unverifiable; it must not poison the
    # symbol's other, independently-checkable sells. The elided sell's own line drops
    # from both sides, so the comparison is the valid sell alone: 999.00 vs 100.00.
    trades = [
        _trade("ABC", "USD", "200", "0", day=1),  # opening buy
        _trade("ABC", "USD", "-100", "100.00", day=2),  # valid closing sell
        _trade("ABC", "USD", "-100", None, day=3),  # closing sell, IBKR realized elided
    ]
    lines = [
        _line("ABC", "USD", "999.00"),  # our FIFO for the valid sell; genuinely wrong
        _line("ABC", "USD", "0.00", elided=True),  # our FIFO for the elided sell
    ]

    report = reconcile_realized_against_ibkr(trades, lines, 2024)

    # The valid sell is cross-checked despite the elided sibling: a real mismatch
    # surfaces instead of the whole symbol vanishing into `incomplete`.
    assert ("ABC", Currency("USD")) not in report.incomplete
    # The blank sell still alarms, orthogonally: being reconciled is no exemption.
    assert report.anomalous_elision == [("ABC", Currency("USD"))]
    [r] = report.value_diffs
    assert (r.symbol, r.currency) == ("ABC", Currency("USD"))
    assert r.computed == Decimal("999.00") and r.ibkr == Decimal("100.00")
    assert r.n_sells == 1  # only the comparable sell counts toward tolerance


def test_synthetic_symbol_with_sibling_elided_sell_is_surfaced(caplog):
    # SYN/USD carries a synthesized-basis sell (gap_fixed) AND a sibling elided
    # sell. The synthetic line must still be surfaced as "not independently confirmed",
    # not demoted to the silent `incomplete` skip just because the symbol also has an
    # elided sell -- the operator explicitly asked the tool to fabricate that basis.
    trades = [
        _trade("SYN", "USD", "-100", "300.00", day=1),  # the synthesized gap sell
        _trade("SYN", "USD", "-50", None, day=2),  # sibling sell, realized elided
    ]
    lines = [
        _line("SYN", "USD", "300.00", gap_fixed=True),  # synthesized basis
        _line("SYN", "USD", "20.00", elided=True),  # the elided sibling
    ]

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.reconcile"):
        report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert ("SYN", Currency("USD")) in [
        (s.symbol, s.currency) for s in report.synthetic
    ]
    assert report.incomplete == []  # surfaced as unconfirmed, not silently skipped
    assert report.reconciled == []  # the only comparable activity is tautological
    # The non-Forex blank sibling alarms orthogonally, even on a surfaced synthetic key.
    assert report.anomalous_elision == [("SYN", Currency("USD"))]
    assert not any("skipped" in rec.getMessage() for rec in caplog.records)


def test_year_filter_ignores_other_periods():
    trades = [
        _trade("ABC", "USD", "-100", "100.00", year=2023),  # prior year -> ignored
        _trade("ABC", "USD", "-50", "30.00", year=2024),
    ]
    lines = [
        _line("ABC", "USD", "999.00", year=2023),  # prior-year line -> ignored
        _line("ABC", "USD", "30.00", year=2024),
    ]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert r.ibkr == Decimal("30.00")  # only the 2024 sell counted
    assert r.computed == Decimal("30.00")  # only the 2024 realized line counted
    assert r.n_sells == 1
    assert r.is_match


def test_ibkr_realized_without_our_line_is_mismatch():
    # IBKR booked realized for a symbol we produced no FIFO line for (dropped sell).
    trades = [_trade("ZZZ", "USD", "-100", "500.00")]

    [r] = reconcile_realized_against_ibkr(trades, [], 2024).reconciled

    assert r.computed is None
    assert r.ibkr == Decimal("500.00")
    assert not r.is_match


def test_our_line_without_ibkr_realized_is_mismatch():
    # We report realized for a symbol IBKR shows no in-year trades for (a dropped or
    # zero-cost-gap sell). A genuine FIFO line with no IBKR counterpart is a mismatch.
    lines = [_line("SYN", "USD", "42.00")]

    [r] = reconcile_realized_against_ibkr([], lines, 2024).reconciled

    assert r.ibkr is None
    assert r.computed == Decimal("42.00")
    assert not r.is_match

    # But a *synthesized* line is backed out of IBKR's own Basis, so its agreement is
    # tautological: it must land in `synthetic`, never in the independent `reconciled`.
    synth = [_line("SYN", "USD", "42.00", gap_fixed=True)]
    report = reconcile_realized_against_ibkr([], synth, 2024)

    assert report.reconciled == []
    [s] = report.synthetic
    assert (s.symbol, s.currency) == ("SYN", Currency("USD"))
    assert s.computed == Decimal("42.00")


def test_each_symbol_compared_in_its_own_currency():
    trades = [
        _trade("AZN", "GBP", "-100", "569.74"),
        _trade("GOOGL", "USD", "-100", "20753.46"),
    ]
    lines = [
        _line("AZN", "GBP", "569.74"),
        _line("GOOGL", "USD", "20753.46"),
    ]

    results = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert [(r.symbol, r.currency) for r in results] == [
        ("AZN", Currency("GBP")),
        ("GOOGL", Currency("USD")),
    ]
    assert all(r.is_match for r in results)


def test_sign_flip_is_sign_diverged():
    # Our FIFO says a gain; IBKR's per-trade realized says a loss. The gain/loss
    # direction disagrees -- a structural signal, flagged apart from a magnitude gap.
    trades = [_trade("FLIP", "USD", "-100", "-2000.00")]
    lines = [_line("FLIP", "USD", "1000.00")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert not r.is_match
    assert r.sign_diverged


def test_same_sign_large_gap_is_not_sign_diverged():
    # Both sides agree it is a gain; only the magnitude differs -- a value gap, not a
    # direction flip, so sign_diverged stays False while is_match is still False.
    trades = [_trade("MAG", "USD", "-100", "5000.00")]
    lines = [_line("MAG", "USD", "1000.00")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert not r.is_match
    assert not r.sign_diverged


def test_within_tolerance_opposite_signs_is_not_divergence():
    # Opposite signs but both sub-cent: the diff is inside the rounding band, so it is a
    # match, and sign_diverged must not fire on rounding noise.
    trades = [_trade("TINY", "USD", "-100", "-0.004")]
    lines = [_line("TINY", "USD", "0.004")]

    [r] = reconcile_realized_against_ibkr(trades, lines, 2024).reconciled

    assert r.is_match
    assert not r.sign_diverged


def test_one_sided_membership_is_not_sign_diverged():
    # A missing side is membership divergence, not a sign flip (it needs both sides).
    trades = [_trade("ONE", "USD", "-100", "500.00")]

    [r] = reconcile_realized_against_ibkr(trades, [], 2024).reconciled

    assert r.computed is None
    assert not r.sign_diverged


def test_report_partitions_divergences_by_class():
    # The report exposes divergence-class views over the independently-checked keys: a
    # sign flip, a magnitude-only gap, and a clean match each land where they belong.
    trades = [
        _trade("FLIP", "USD", "-100", "-2000.00"),  # mine +1000 vs IBKR -2000: sign
        _trade("GAP", "USD", "-100", "5000.00"),  # mine +1000 vs IBKR +5000: magnitude
        _trade("OK", "USD", "-100", "10.00"),  # mine +10 vs IBKR +10: match
    ]
    lines = [
        _line("FLIP", "USD", "1000.00"),
        _line("GAP", "USD", "1000.00"),
        _line("OK", "USD", "10.00"),
    ]

    report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert [r.symbol for r in report.sign_flips] == ["FLIP"]
    assert [r.symbol for r in report.value_diffs] == ["GAP"]
