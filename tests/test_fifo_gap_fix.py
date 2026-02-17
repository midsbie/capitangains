from __future__ import annotations

import datetime as dt
from decimal import Decimal

from capitangains.reporting.extract import TradeRow
from capitangains.reporting.fifo import FifoMatcher
from capitangains.reporting.fx import FxTable
from capitangains.reporting.report_builder import ReportBuilder


def _make_fx(rates: dict[tuple[str, str], Decimal]) -> FxTable:
    ft = FxTable()
    for (ccy, d), v in rates.items():
        c = ccy.upper()
        ft.data[c][d] = v
    for c, m in ft.data.items():
        ft.date_index[c] = sorted(m.keys())
    return ft


def _buy(
    symbol: str, date: dt.date, qty: str, proceeds: str, comm: str, ccy: str = "USD"
) -> TradeRow:
    # For buys, proceeds negative; comm negative.  Basis allocation computed from these
    # by matcher
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency=ccy,
        symbol=symbol,
        datetime_str=date.isoformat(),
        date=date,
        quantity=Decimal(qty),
        t_price=Decimal("0"),
        proceeds=Decimal(proceeds),
        comm_fee=Decimal(comm),
        code="P",
    )


def _sell(
    symbol: str,
    date: dt.date,
    qty: str,
    proceeds: str,
    comm: str,
    basis: str | None,
    ccy: str = "USD",
) -> TradeRow:
    # For sells, proceeds positive; comm typically negative; basis in IBKR is negative
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency=ccy,
        symbol=symbol,
        datetime_str=date.isoformat(),
        date=date,
        quantity=Decimal(qty),
        t_price=Decimal("0"),
        proceeds=Decimal(proceeds),
        comm_fee=Decimal(comm),
        code="P",
        basis_ccy=(Decimal(basis) if basis is not None else None),
        realized_pl_ccy=None,
    )


def test_fifo_no_fix_records_gap_and_zero_cost():
    m = FifoMatcher(fix_sell_gaps=False)
    # Buy 100 cost 1000
    m.ingest_trade(_buy("ABC", dt.date(2024, 1, 1), "100", "-1000", "0", "USD"))
    # Sell 120 proceeds 1200, basis -1200 (not used when no fix)
    rl = m.ingest_trade(
        _sell("ABC", dt.date(2024, 2, 1), "-120", "1200", "0", "-1200", "USD")
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
    # Gap event recorded
    assert len(m.gap_events) == 1 and m.gap_events[0].fixed is False


def test_fifo_auto_fix_creates_synthetic_leg_and_matches_basis():
    m = FifoMatcher(fix_sell_gaps=True)
    m.ingest_trade(_buy("XYZ", dt.date(2024, 1, 1), "100", "-1000", "0", "USD"))
    # SELL 120, proceeds 1200, IBKR Basis -1200 -> target alloc 1200
    rl = m.ingest_trade(
        _sell("XYZ", dt.date(2024, 2, 1), "-120", "1200", "0", "-1200", "USD")
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


def test_fifo_auto_fix_missing_basis_falls_back_zero_cost():
    m = FifoMatcher(fix_sell_gaps=True)
    m.ingest_trade(_buy("DEF", dt.date(2024, 1, 1), "50", "-500", "0", "USD"))
    # SELL 60, proceeds 600, Basis missing
    rl = m.ingest_trade(
        _sell("DEF", dt.date(2024, 2, 1), "-60", "600", "0", None, "USD")
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is False
    assert len(rl.legs) == 2
    assert rl.legs[1].alloc_cost_ccy == Decimal("0")
    # Gap event recorded (not fixed)
    assert any(ev.fixed is False for ev in m.gap_events)


def test_fifo_auto_fix_negative_residual_within_tolerance_clamps():
    m = FifoMatcher(fix_sell_gaps=True, gap_tolerance=Decimal("0.02"))
    # Buy 90 cost 900
    m.ingest_trade(_buy("CLP", dt.date(2024, 1, 1), "90", "-900", "0", "USD"))
    # SELL 100 with IBKR Basis slightly less than matched alloc
    # (residual = -0.01 -> clamp to 0)
    rl = m.ingest_trade(
        _sell("CLP", dt.date(2024, 2, 1), "-100", "1000", "0", "-899.99", "USD")
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is True  # synthetic leg created (qty=10) but zero cost
    assert rl.legs[-1].qty == Decimal("10")
    assert rl.legs[-1].alloc_cost_ccy == Decimal("0.00000000")


def test_fifo_auto_fix_negative_residual_beyond_tolerance_fallback():
    m = FifoMatcher(fix_sell_gaps=True, gap_tolerance=Decimal("0.02"))
    # Buy 90 cost 900
    m.ingest_trade(_buy("FLT", dt.date(2024, 1, 1), "90", "-900", "0", "USD"))
    # SELL 100 with IBKR Basis much less than matched alloc (residual = -5 -> fallback)
    rl = m.ingest_trade(
        _sell("FLT", dt.date(2024, 2, 1), "-100", "1000", "0", "-895", "USD")
    )
    assert rl is not None
    assert rl.has_gap is True
    assert rl.gap_fixed is False
    assert rl.legs[-1].qty == Decimal("10")
    assert rl.legs[-1].alloc_cost_ccy == Decimal("0")


def test_fifo_synthetic_leg_fx_conversion_and_annex_dates():
    m = FifoMatcher(fix_sell_gaps=True)
    # Buy 100 cost 1000 USD on 2024-01-01
    m.ingest_trade(_buy("EURX", dt.date(2024, 1, 1), "100", "-1000", "0", "USD"))
    # Sell 120 on 2024-02-01, proceeds 1200, Basis -1200 so residual = 200
    rl = m.ingest_trade(
        _sell("EURX", dt.date(2024, 2, 1), "-120", "1200", "0", "-1200", "USD")
    )
    assert rl is not None and rl.gap_fixed is True

    rb = ReportBuilder(year=2024)
    rb.add_realized(rl)

    fx = _make_fx(
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
