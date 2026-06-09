from __future__ import annotations

import datetime as dt
from decimal import Decimal

from capitangains.reporting.fifo_domain import GapResolution
from capitangains.reporting.report_builder import ReportBuilder
from tests.support import buy, make_fx, make_matcher, sell


def test_fifo_no_fix_records_gap_and_zero_cost():
    m = make_matcher()
    # Buy 100 cost 1000
    m.ingest_trade(buy("ABC", dt.date(2024, 1, 1), "100", "-1000", "0", ccy="USD"))
    # Sell 120 proceeds 1200, basis -1200 (not used when no fix)
    rl = m.ingest_trade(
        sell("ABC", dt.date(2024, 2, 1), "-120", "1200", "0", basis="-1200", ccy="USD")
    )

    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is False
    # First leg allocates full buy cost; remainder is zero-cost
    assert len(rl.legs) == 2
    assert rl.legs[0].qty == Decimal("100")
    assert rl.legs[0].alloc_cost_ccy == Decimal("1000")
    assert rl.legs[1].qty == Decimal("20")
    assert rl.legs[1].alloc_cost_ccy == Decimal("0")
    # Realized = 1200 - 1000
    assert rl.realized_pl_ccy == Decimal("200.00")
    # Gap event recorded as unacknowledged
    assert len(m.gap_events) == 1
    assert m.gap_events[0].outcome is GapResolution.UNACKNOWLEDGED


def test_fifo_auto_fix_creates_synthetic_leg_and_matches_basis():
    m = make_matcher(frozenset({("XYZ", dt.date(2024, 2, 1))}))
    m.ingest_trade(buy("XYZ", dt.date(2024, 1, 1), "100", "-1000", "0", ccy="USD"))
    # SELL 120, proceeds 1200, IBKR Basis -1200 -> target alloc 1200. Realized (1200 + 0
    # - 1200 = 0) vouches for the Basis, the precondition for synthesis.
    rl = m.ingest_trade(
        sell(
            "XYZ",
            dt.date(2024, 2, 1),
            "-120",
            "1200",
            "0",
            basis="-1200",
            realized="0",
            ccy="USD",
        )
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is True
    assert len(rl.legs) == 2
    # Synthetic leg flagged and dated at sell date
    synth = rl.legs[1]
    assert synth.synthetic is True
    assert synth.buy_date == dt.date(2024, 2, 1)
    assert synth.qty == Decimal("20")
    # Residual cost brings total alloc to 1200
    total_alloc = rl.legs[0].alloc_cost_ccy + synth.alloc_cost_ccy
    assert total_alloc == Decimal("1200.00000000")
    # Realized matches IBKR per-trade: 1200 net - 1200 alloc = 0.00
    assert rl.realized_pl_ccy == Decimal("0.00")


def test_fifo_auto_fix_missing_basis_is_defective():
    m = make_matcher(frozenset({("DEF", dt.date(2024, 2, 1))}))
    m.ingest_trade(buy("DEF", dt.date(2024, 1, 1), "50", "-500", "0", ccy="USD"))
    # SELL 60, proceeds 600, Basis missing
    rl = m.ingest_trade(
        sell("DEF", dt.date(2024, 2, 1), "-60", "600", "0", basis=None, ccy="USD")
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is False
    assert len(rl.legs) == 2
    assert rl.legs[1].alloc_cost_ccy == Decimal("0")
    # Acknowledged but unusable: a missing Basis is DEFECTIVE (fatal at the boundary)
    assert m.gap_events[0].outcome is GapResolution.DEFECTIVE


def test_fifo_auto_fix_negative_residual_within_tolerance_clamps():
    # The default synthesis tolerance (0.02) is what makes residual -0.01 clamp below.
    m = make_matcher(frozenset({("CLP", dt.date(2024, 2, 1))}))
    # Buy 90 cost 900
    m.ingest_trade(buy("CLP", dt.date(2024, 1, 1), "90", "-900", "0", ccy="USD"))
    # SELL 100 with IBKR Basis slightly less than matched alloc
    # (residual = -0.01 -> clamp to 0). Realized (1000 + 0 - 899.99) vouches for the
    # Basis so synthesis is not refused.
    rl = m.ingest_trade(
        sell(
            "CLP",
            dt.date(2024, 2, 1),
            "-100",
            "1000",
            "0",
            basis="-899.99",
            realized="100.01",
            ccy="USD",
        )
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is True  # synthetic leg created (qty=10) but zero cost
    assert rl.legs[-1].qty == Decimal("10")
    assert rl.legs[-1].alloc_cost_ccy == Decimal("0.00000000")


def test_fifo_auto_fix_negative_residual_beyond_tolerance_is_defective():
    # The default synthesis tolerance (0.02) is what makes residual -5 DEFECTIVE below.
    m = make_matcher(frozenset({("FLT", dt.date(2024, 2, 1))}))
    # Buy 90 cost 900
    m.ingest_trade(buy("FLT", dt.date(2024, 1, 1), "90", "-900", "0", ccy="USD"))
    # SELL 100 with IBKR Basis much less than matched alloc (residual = -5 -> fallback)
    rl = m.ingest_trade(
        sell("FLT", dt.date(2024, 2, 1), "-100", "1000", "0", basis="-895", ccy="USD")
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is False
    assert rl.legs[-1].qty == Decimal("10")
    assert rl.legs[-1].alloc_cost_ccy == Decimal("0")
    assert m.gap_events[0].outcome is GapResolution.DEFECTIVE


def test_fifo_synthetic_leg_fx_conversion_and_annex_dates():
    m = make_matcher(frozenset({("EURX", dt.date(2024, 2, 1))}))
    # Buy 100 cost 1000 USD on 2024-01-01
    m.ingest_trade(buy("EURX", dt.date(2024, 1, 1), "100", "-1000", "0", ccy="USD"))
    # Sell 120 on 2024-02-01, proceeds 1200, Basis -1200 so residual = 200. Realized
    # (1200 + 0 - 1200 = 0) vouches for the Basis, the precondition for synthesis.
    rl = m.ingest_trade(
        sell(
            "EURX",
            dt.date(2024, 2, 1),
            "-120",
            "1200",
            "0",
            basis="-1200",
            realized="0",
            ccy="USD",
        )
    )
    assert rl is not None and rl.gap_fixed is True

    rb = ReportBuilder(year=2024)
    rb.add_realized(rl)

    fx = make_fx(
        {
            ("USD", "2024-01-01"): Decimal("0.9"),
            ("USD", "2024-02-01"): Decimal("0.8"),
        }
    )
    rb.convert_eur(fx)

    # EUR alloc = 1000 * 0.9 + 200 * 0.8 = 900 + 160 = 1060 -> 2 decimals
    assert rl.alloc_cost_eur == Decimal("1060.00")
    # EUR proceeds = (1200) * 0.8 = 960.00; realized = 960 - 1060 = -100.00
    assert rl.sell_net_eur == Decimal("960.00")
    assert rl.realized_pl_eur == Decimal("-100.00")
    # Legs should have per-leg EUR alloc and proceeds share
    assert all(leg.alloc_cost_eur is not None for leg in rl.legs)
    assert all(leg.proceeds_share_eur is not None for leg in rl.legs)


def test_fifo_auto_fix_rejects_basis_inconsistent_with_realized():
    m = make_matcher(frozenset({("BAD", dt.date(2024, 2, 1))}))
    # No buy history: the whole 120 is a gap. IBKR Basis -1200 would synthesize a 1200
    # cost, but IBKR's own Realized P/L (999) contradicts the identity (1200 + 0 - 1200
    # = 0), so the Basis cell is corrupt: synthesis flags it DEFECTIVE (the boundary
    # then aborts) instead of fabricating a cost.
    closing = sell(
        "BAD", dt.date(2024, 2, 1), "-120", "1200", "0", basis="-1200", ccy="USD"
    )
    closing.realized_pl_ccy = Decimal("999")
    rl = m.ingest_trade(closing)
    assert rl is not None and rl.gap_fixed is False
    assert m.gap_events[0].outcome is GapResolution.DEFECTIVE
    assert "Basis" in m.gap_events[0].message


def test_fifo_auto_fix_accepts_synthesis_when_realized_confirms_basis():
    m = make_matcher(frozenset({("OKAY", dt.date(2024, 2, 1))}))
    # IBKR Basis -1200 and Realized 0 are mutually consistent (1200 + 0 - 1200 = 0), so
    # synthesis proceeds: the realized_getter wiring must not reject a valid Basis.
    closing = sell(
        "OKAY", dt.date(2024, 2, 1), "-120", "1200", "0", basis="-1200", ccy="USD"
    )
    closing.realized_pl_ccy = Decimal("0")
    rl = m.ingest_trade(closing)
    assert rl is not None and rl.gap_fixed is True
    assert rl.legs[-1].synthetic is True
