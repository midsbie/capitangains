"""End-to-end reconciliation oracle: real FIFO matching vs IBKR's realized column.

`test_reconcile.py` injects the computed side directly, which keeps the partition logic
honest but never runs the cash math, so it cannot catch a basis/commission bug.  Here
computed comes from the *real* FifoMatcher (lot matching, quantity allocation,
commission folding); reconciling it against an independently chosen IBKR Realized P/L is
a genuine, non-tautological cross-check. Agreement corroborates the FIFO basis; the
deliberate-bug cases below prove a real discrepancy is flagged.

Footgun guarded throughout: every sell here passes realized (IBKR's per-trade
Realized P/L). Leaving it None marks the closing trade "incomplete", which silently
empties reconciled -- so the oracle asserts len(report.reconciled) > 0.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from capitangains.reporting.reconcile import reconcile_realized_against_ibkr
from tests.support import buy, ingest, make_matcher, sell, transfer_row


def test_oracle_multilot_reconciles_against_ibkr_realized():
    """Real FIFO across two buy lots reproduces IBKR's realized P/L within tolerance.

    GOOGL: 200@138 + 250@138 bought (incl. commissions), 450@188 sold (incl.
    commission), USD. IBKR per-trade realized 22493.07342. Driving the real matcher
    makes the comparison genuinely test lot-matching completeness, quantity allocation,
    and commission folding -- none of which the inject-computed unit tests touch.
    """
    m = make_matcher()
    trades = [
        buy("GOOGL", dt.date(2024, 1, 5), "200", "-27600", "-1.50"),
        buy("GOOGL", dt.date(2024, 2, 9), "250", "-34500", "-2.00"),
        sell(
            "GOOGL",
            dt.date(2024, 6, 10),
            "-450",
            "84600",
            "-3.43",
            realized="22493.07342",
        ),
    ]
    realized = ingest(m, trades)

    # Real FIFO consumed both lots with no gap: a genuine, fully-matched close.
    [rl] = realized
    assert len(rl.legs) == 2
    assert rl.has_gap is False

    report = reconcile_realized_against_ibkr(trades, realized, 2024)

    assert len(report.reconciled) > 0  # guards the None-realized footgun
    assert report.synthetic == []
    assert all(r.is_match for r in report.reconciled)
    [r] = report.reconciled
    assert r.computed == Decimal("22493.07")  # 84596.57 net - 62103.50 basis
    assert r.ibkr == Decimal("22493.07342")


def test_dropped_buy_lot_is_reconciled_mismatch_not_excluded():
    """A zero-cost gap (missing buy lot, no auto-fix) must MISMATCH inside `reconciled`.

    Guards against over-broad exclusion: only *synthesized* lines are split out. A gap
    left at zero cost keeps gap_fixed=False, so it stays in the independent set and
    surfaces the inflated gain as a mismatch rather than hiding in `synthetic`.
    """
    m = make_matcher()
    # Only 200 of the 450 sold are covered; the 250-share lot is missing.
    trades = [
        buy("MSFT", dt.date(2024, 1, 5), "200", "-27600", "-1.50"),
        sell(
            "MSFT",
            dt.date(2024, 6, 10),
            "-450",
            "84600",
            "-3.43",
            realized="22493.07342",
        ),
    ]
    realized = ingest(m, trades)
    assert realized[0].has_gap is True and realized[0].gap_fixed is False

    report = reconcile_realized_against_ibkr(trades, realized, 2024)

    assert report.synthetic == []  # a zero-cost gap is not synthetic
    assert ("MSFT", "USD") in {(r.symbol, r.currency) for r in report.reconciled}
    [r] = report.reconciled
    assert not r.is_match


def test_dropped_buy_commission_is_mismatch_proving_non_tautology():
    """A fully-matched close whose buy commission is missing MISMATCHes by the fee.

    Proves the cross-check is not tautological for genuine lines: there is no gap and
    the quantities tie out, yet because our basis omits the buy fee our realized P/L
    diverges from IBKR's by exactly that fee -- and the reconciler catches it.
    """
    m = make_matcher()
    trades = [
        # Buy commission dropped to 0 (the real fill paid 50): basis is low by 50.
        buy("AMD", dt.date(2024, 1, 5), "450", "-62100", "0"),
        # IBKR's realized reflects the true with-commission basis (62150).
        sell(
            "AMD", dt.date(2024, 6, 10), "-450", "84600", "-3.43", realized="22446.57"
        ),
    ]
    realized = ingest(m, trades)
    assert (
        realized[0].has_gap is False
    )  # full match -- the discrepancy is basis, not a gap

    report = reconcile_realized_against_ibkr(trades, realized, 2024)

    [r] = report.reconciled
    assert not r.is_match
    assert r.computed == Decimal("22496.57")  # 84596.57 - 62100 (fee omitted)
    assert r.diff == Decimal("50.00")


def test_transfer_in_basis_divergence_is_flagged():
    """A transfer-in seeded from market_value MISMATCHes when that proxy is wrong.

    `ingest_transfer` uses market_value as the cost-basis proxy (fifo.py:84). When
    it differs from the true basis, our realized P/L is off by the gap; setting IBKR's
    realized to the true-basis value documents and guards that approximation risk.
    """
    m = make_matcher()
    # Proxy basis 5000 vs a true basis of 4000 -> IBKR realized 2000 vs our 1000.
    m.ingest_transfer(
        transfer_row(
            symbol="XFER",
            date=dt.date(2024, 1, 3),
            quantity="100",
            market_value="5000",
        )
    )
    closing = sell(
        "XFER", dt.date(2024, 6, 10), "-100", "6000", "0", realized="2000.00"
    )
    [rl] = ingest(m, [closing])
    assert rl.has_gap is False  # the transfer lot fully covers the sale

    report = reconcile_realized_against_ibkr([closing], [rl], 2024)

    assert report.synthetic == []  # a transfer lot is not a synthesized gap
    [r] = report.reconciled
    assert not r.is_match
    assert r.computed == Decimal("1000.00")  # 6000 - 5000 (market_value proxy)
    assert r.ibkr == Decimal("2000.00")
    assert r.diff == Decimal("1000.00")


def test_synthesized_gap_sell_lands_in_synthetic_not_reconciled():
    """A synthesized-basis sell is split into `synthetic`, never the independent set.

    Closes the loop with #5's exclusion: no buy coverage + IBKR Basis + auto-fix means
    the cost is backed out of the same Basis IBKR used, so the cross-check is
    tautological. It would *pass* (is_match) -- which is exactly why it must not be
    counted as an independent confirmation.
    """
    m = make_matcher(frozenset({("SYN", dt.date(2024, 6, 10))}))
    # No buys at all: the whole 100 is a gap, synthesized from IBKR Basis -5000.
    closing = sell(
        "SYN",
        dt.date(2024, 6, 10),
        "-100",
        "6000",
        "0",
        basis="-5000",
        realized="1000.00",
    )
    [rl] = ingest(m, [closing])
    assert rl.gap_fixed is True

    report = reconcile_realized_against_ibkr([closing], [rl], 2024)

    assert report.reconciled == []
    [s] = report.synthetic
    assert (s.symbol, s.currency) == ("SYN", "USD")
    assert s.computed == s.ibkr == Decimal("1000.00")
    assert s.is_match  # would pass -- hence the need to exclude it from `reconciled`
