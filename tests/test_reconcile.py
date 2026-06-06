import datetime as dt
import logging
from decimal import Decimal

from capitangains.reporting.extract import TradeRow
from capitangains.reporting.fifo_domain import RealizedLine
from capitangains.reporting.reconcile import reconcile_realized_against_ibkr


def _trade(symbol, currency, quantity, realized, *, year=2024, month=6, day=1):
    """A minimal stock TradeRow carrying IBKR's per-trade `Realized P/L` (trade ccy)."""
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency=currency,
        symbol=symbol,
        datetime_str=f"{year}-{month:02d}-{day:02d}, 10:00:00",
        date=dt.date(year, month, day),
        quantity=Decimal(quantity),
        t_price=Decimal("1"),
        proceeds=Decimal("0"),
        comm_fee=Decimal("0"),
        code="",
        realized_pl_ccy=None if realized is None else Decimal(realized),
    )


def _line(symbol, ccy, realized, *, gap_fixed=False, year=2024):
    """A minimal RealizedLine carrying only what the reconciler reads.

    The reconciler keys off ``symbol``/``currency``, sums ``realized_pl_ccy``, filters
    on ``sell_date.year`` and partitions on ``gap_fixed``; other fields are zeroed.
    """
    return RealizedLine(
        symbol=symbol,
        currency=ccy,
        sell_date=dt.date(year, 6, 1),
        sell_qty=Decimal("0"),
        sell_gross_ccy=Decimal("0"),
        sell_comm_ccy=Decimal("0"),
        sell_net_ccy=Decimal("0"),
        legs=[],
        realized_pl_ccy=Decimal(realized),
        has_gap=gap_fixed,  # synthesis only happens on a gap; keep the pair consistent
        gap_fixed=gap_fixed,
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

    assert (r.symbol, r.currency) == ("GOOGL", "USD")
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


def test_elided_ibkr_realized_skips_symbol(caplog):
    trades = [
        _trade("ABC", "USD", "100", "0"),
        _trade("ABC", "USD", "-100", None),  # closing trade, realized value elided
    ]
    lines = [_line("ABC", "USD", "50.00")]

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.reconcile"):
        report = reconcile_realized_against_ibkr(trades, lines, 2024)

    assert report.reconciled == []  # IBKR total untrustworthy -> not reconciled
    assert report.incomplete == [("ABC", "USD")]
    assert any(
        "skipped" in rec.getMessage() and "ABC" in rec.getMessage()
        for rec in caplog.records
    )


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
    assert (s.symbol, s.currency) == ("SYN", "USD")
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
        ("AZN", "GBP"),
        ("GOOGL", "USD"),
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
