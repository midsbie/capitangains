import datetime as dt
from decimal import Decimal

import pytest
from fixtures import Trade, Transfer

from capitangains.reporting.events import EventRecorder
from capitangains.reporting.fifo import FifoMatcher
from capitangains.reporting.fifo_domain import GapEvent, Lot
from capitangains.reporting.gap_policy import GapPolicy
from capitangains.reporting.positions import PositionBook


class DummyPolicy(GapPolicy):
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, trade, qty_remaining, legs, alloc_cost_so_far):
        self.calls += 1
        return (
            legs,
            alloc_cost_so_far,
            GapEvent(
                symbol=trade.symbol,
                date=trade.date,
                remaining_qty=Decimal("0"),
                currency=trade.currency,
                message="dummy",
                fixed=True,
            ),
        )


class NoneEventPolicy(GapPolicy):
    def resolve(self, trade, qty_remaining, legs, alloc_cost_so_far):
        legs.append(
            {
                "buy_date": None,
                "qty": qty_remaining,
                "lot_qty_before": Decimal("0"),
                "alloc_cost_ccy": Decimal("0"),
            }
        )
        return legs, alloc_cost_so_far, None


def _trade(
    symbol: str,
    qty: Decimal,
    *,
    proceeds: Decimal,
    comm: Decimal,
    currency: str = "USD",
) -> Trade:
    return Trade(
        symbol=symbol,
        quantity=qty,
        proceeds=proceeds,
        comm_fee=comm,
        currency=currency,
        date=dt.date(2024, 1, 1),
    )


def test_fifo_matcher_buy_uses_injected_position_book():
    book = PositionBook()
    matcher = FifoMatcher(positions=book)
    trade = _trade("ABC", Decimal("10"), proceeds=Decimal("-100"), comm=Decimal("-1"))

    assert matcher.ingest_trade(trade) is None
    assert book.has_position("ABC", "USD") is True


def test_fifo_matcher_sell_uses_gap_policy_and_recorder():
    book = PositionBook()
    book.append_buy(
        "ABC", Lot(dt.date(2023, 12, 1), Decimal("5"), Decimal("50"), "USD")
    )
    policy = DummyPolicy()
    recorder = EventRecorder()
    matcher = FifoMatcher(positions=book, gap_policy=policy, recorder=recorder)

    trade = _trade("ABC", Decimal("-10"), proceeds=Decimal("100"), comm=Decimal("0"))
    line = matcher.ingest_trade(trade)

    assert line is not None and line.has_gap is True and line.gap_fixed is True
    assert policy.calls == 1
    assert recorder.gap_events[0].message == "dummy"


def test_fifo_matcher_gap_policy_can_skip_event():
    book = PositionBook()
    policy = NoneEventPolicy()
    matcher = FifoMatcher(positions=book, gap_policy=policy)

    trade = _trade("ABC", Decimal("-5"), proceeds=Decimal("50"), comm=Decimal("0"))
    line = matcher.ingest_trade(trade)

    assert line is not None and line.has_gap is True
    assert matcher.gap_events == []


def test_fifo_matcher_validates_quantities():
    matcher = FifoMatcher()

    with pytest.raises(ValueError):
        matcher.ingest_trade(
            _trade("ABC", Decimal("0"), proceeds=Decimal("0"), comm=Decimal("0"))
        )

    with pytest.raises(ValueError):
        matcher._ingest_buy(
            _trade("ABC", Decimal("-1"), proceeds=Decimal("0"), comm=Decimal("0"))
        )

    with pytest.raises(ValueError):
        matcher._ingest_sell(
            _trade("ABC", Decimal("1"), proceeds=Decimal("0"), comm=Decimal("0"))
        )


# --- Transfer interleaving tests ---


def _transfer(
    symbol: str,
    date: dt.date,
    qty: str,
    direction: str,
    market_value: str,
    currency: str = "USD",
) -> Transfer:
    return Transfer(
        date=date,
        symbol=symbol,
        quantity=Decimal(qty),
        currency=currency,
        direction=direction,
        market_value=Decimal(market_value),
    )


def test_transfer_out_after_buy_depletes_lots_before_sell():
    """buy(100) -> transfer-out(100) -> sell(100): sell should gap."""
    matcher = FifoMatcher()

    matcher.ingest_trade(
        _trade("ABC", Decimal("100"), proceeds=Decimal("-1000"), comm=Decimal("0"))
    )
    matcher.ingest_transfer(_transfer("ABC", dt.date(2024, 2, 1), "100", "Out", "1000"))
    line = matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 3, 1),
            symbol="ABC",
            quantity=Decimal("-100"),
            currency="USD",
            proceeds=Decimal("1200"),
            comm_fee=Decimal("0"),
        )
    )

    assert line is not None
    assert line.has_gap is True


def test_transfer_in_before_sell_funds_sell():
    """transfer-in(100) -> sell(50): sell should match the transferred lot."""
    matcher = FifoMatcher()

    matcher.ingest_transfer(_transfer("XYZ", dt.date(2024, 1, 1), "100", "In", "1000"))
    line = matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 2, 1),
            symbol="XYZ",
            quantity=Decimal("-50"),
            currency="USD",
            proceeds=Decimal("600"),
            comm_fee=Decimal("0"),
        )
    )

    assert line is not None
    assert line.has_gap is False
    assert len(line.legs) == 1
    assert line.legs[0].transferred is True
    assert line.legs[0].alloc_cost_ccy == Decimal("500")  # 50/100 * 1000


def test_transfer_in_after_sell_does_not_fund_earlier_sell():
    """sell(100) -> transfer-in(100): sell should gap (lot doesn't exist yet)."""
    matcher = FifoMatcher()

    line = matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 1, 1),
            symbol="ABC",
            quantity=Decimal("-100"),
            currency="USD",
            proceeds=Decimal("1200"),
            comm_fee=Decimal("0"),
        )
    )
    matcher.ingest_transfer(_transfer("ABC", dt.date(2024, 2, 1), "100", "In", "1000"))

    assert line is not None
    assert line.has_gap is True


def test_buy_transfer_out_sell_partial_depletes_correctly():
    """buy(100) -> transfer-out(50) -> sell(50): sell matches remaining 50."""
    matcher = FifoMatcher()

    matcher.ingest_trade(
        _trade("ABC", Decimal("100"), proceeds=Decimal("-1000"), comm=Decimal("0"))
    )
    matcher.ingest_transfer(_transfer("ABC", dt.date(2024, 2, 1), "50", "Out", "500"))
    line = matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 3, 1),
            symbol="ABC",
            quantity=Decimal("-50"),
            currency="USD",
            proceeds=Decimal("600"),
            comm_fee=Decimal("0"),
        )
    )

    assert line is not None
    assert line.has_gap is False
    assert line.legs[0].qty == Decimal("50")
    assert line.legs[0].alloc_cost_ccy == Decimal("500")
    assert line.realized_pl_ccy == Decimal("100.00")


def test_sell_matches_only_lots_in_same_currency():
    """Buy XYZ in EUR, then sell XYZ in USD: lots must not cross-match."""
    matcher = FifoMatcher()

    # Buy 100 XYZ denominated in EUR (basis = 1000 EUR)
    matcher.ingest_trade(
        _trade(
            "XYZ",
            Decimal("100"),
            proceeds=Decimal("-1000"),
            comm=Decimal("0"),
            currency="EUR",
        )
    )

    # Sell 100 XYZ denominated in USD — should NOT consume the EUR lot
    line = matcher.ingest_trade(
        _trade(
            "XYZ",
            Decimal("-100"),
            proceeds=Decimal("1200"),
            comm=Decimal("0"),
            currency="USD",
        )
    )

    assert line is not None
    # The sell must report a gap: no USD lots exist for XYZ
    assert line.has_gap is True
    # The EUR lot must remain unconsumed
    assert matcher.positions.has_position("XYZ", "EUR") is True


def test_transfer_in_and_buy_both_fund_sell_in_fifo_order():
    """transfer-in(50) -> buy(50) -> sell(100): FIFO consumes transfer lot first."""
    matcher = FifoMatcher()

    matcher.ingest_transfer(_transfer("ABC", dt.date(2024, 1, 1), "50", "In", "400"))
    matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 2, 1),
            symbol="ABC",
            quantity=Decimal("50"),
            currency="USD",
            proceeds=Decimal("-600"),
            comm_fee=Decimal("0"),
        )
    )
    line = matcher.ingest_trade(
        Trade(
            date=dt.date(2024, 3, 1),
            symbol="ABC",
            quantity=Decimal("-100"),
            currency="USD",
            proceeds=Decimal("1200"),
            comm_fee=Decimal("0"),
        )
    )

    assert line is not None
    assert line.has_gap is False
    assert len(line.legs) == 2
    # First leg: transfer lot (FIFO order)
    assert line.legs[0].transferred is True
    assert line.legs[0].qty == Decimal("50")
    assert line.legs[0].alloc_cost_ccy == Decimal("400")
    # Second leg: buy lot
    assert line.legs[1].transferred is False
    assert line.legs[1].qty == Decimal("50")
    assert line.legs[1].alloc_cost_ccy == Decimal("600")
