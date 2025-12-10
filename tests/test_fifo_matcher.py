import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

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
):
    return SimpleNamespace(
        symbol=symbol,
        quantity=qty,
        proceeds=proceeds,
        comm_fee=comm,
        currency=currency,
        date=dt.date(2024, 1, 1),
        basis_ccy=None,
    )


def test_fifo_matcher_buy_uses_injected_position_book():
    book = PositionBook()
    matcher = FifoMatcher(positions=book)
    trade = _trade("ABC", Decimal("10"), proceeds=Decimal("-100"), comm=Decimal("-1"))

    assert matcher.ingest(trade) is None
    assert book.has_position("ABC") is True


def test_fifo_matcher_sell_uses_gap_policy_and_recorder():
    book = PositionBook()
    book.append_buy(
        "ABC", Lot(dt.date(2023, 12, 1), Decimal("5"), Decimal("50"), "USD")
    )
    policy = DummyPolicy()
    recorder = EventRecorder()
    matcher = FifoMatcher(positions=book, gap_policy=policy, recorder=recorder)

    trade = _trade("ABC", Decimal("-10"), proceeds=Decimal("100"), comm=Decimal("0"))
    line = matcher.ingest(trade)

    assert line is not None and line.has_gap is True and line.gap_fixed is True
    assert policy.calls == 1
    assert recorder.gap_events[0].message == "dummy"


def test_fifo_matcher_gap_policy_can_skip_event():
    book = PositionBook()
    policy = NoneEventPolicy()
    matcher = FifoMatcher(positions=book, gap_policy=policy)

    trade = _trade("ABC", Decimal("-5"), proceeds=Decimal("50"), comm=Decimal("0"))
    line = matcher.ingest(trade)

    assert line.has_gap is True
    assert matcher.gap_events == []


def test_fifo_matcher_validates_quantities():
    matcher = FifoMatcher()

    with pytest.raises(ValueError):
        matcher.ingest(
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
