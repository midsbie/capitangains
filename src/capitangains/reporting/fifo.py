from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from .events import EventRecorder
from .fifo_domain import GapEvent, Lot, RealizedLine
from .gap_policy import BasisSynthesisPolicy, GapPolicy, StrictGapPolicy
from .money import abs_decimal
from .positions import PositionBook
from .realized_builder import build_realized_line
from .trade_math import buy_cost_ccy

logger = logging.getLogger(__name__)

_DEFAULT_GAP_TOLERANCE = Decimal("0.02")


def _default_basis_getter(trade: Any) -> Decimal | None:
    return getattr(trade, "basis_ccy", None)


class FifoMatcher:
    def __init__(
        self,
        *,
        positions: Optional[PositionBook] = None,
        gap_policy: Optional[GapPolicy] = None,
        recorder: Optional[EventRecorder] = None,
        fix_sell_gaps: Optional[bool] = None,
        gap_tolerance: Optional[Decimal] = None,
    ) -> None:
        self.positions = positions or PositionBook()
        self.recorder = recorder or EventRecorder()

        self.fix_sell_gaps = bool(fix_sell_gaps) if fix_sell_gaps is not None else False
        self.gap_tolerance = (
            gap_tolerance if gap_tolerance is not None else _DEFAULT_GAP_TOLERANCE
        )
        self._gap_policy = self._resolve_gap_policy(gap_policy)

    def _resolve_gap_policy(self, policy: Optional[GapPolicy]) -> GapPolicy:
        if policy is not None:
            return policy
        if self.fix_sell_gaps:
            return BasisSynthesisPolicy(
                tolerance=self.gap_tolerance,
                basis_getter=_default_basis_getter,
            )
        return StrictGapPolicy()

    @property
    def gap_events(self) -> list[GapEvent]:
        return self.recorder.gap_events

    def ingest(self, trade: Any) -> Optional[RealizedLine]:
        qty = trade.quantity
        if qty > 0:
            return self._ingest_buy(trade)
        elif qty < 0:
            return self._ingest_sell(trade)
        else:
            raise ValueError("trade quantity cannot be zero")
        return None

    def _ingest_buy(self, trade: Any) -> None:
        if trade.quantity <= 0:
            raise ValueError("buy trades must have positive quantity")
        lot = Lot(
            buy_date=trade.date,
            qty=trade.quantity,
            basis_ccy=buy_cost_ccy(trade.proceeds, trade.comm_fee),
            currency=trade.currency,
        )
        self.positions.append_buy(trade.symbol, lot)
        return None

    def _ingest_sell(self, trade: Any) -> RealizedLine:
        if trade.quantity >= 0:
            raise ValueError("sell trades must have negative quantity")
        qty_to_sell = abs_decimal(trade.quantity)

        legs, alloc_cost_ccy, qty_remaining = self.positions.consume_fifo(
            trade.symbol, qty_to_sell
        )

        gap_event: GapEvent | None = None
        has_gap = qty_remaining > 0
        gap_fixed = False

        if has_gap:
            legs, alloc_cost_ccy, gap_event = self._gap_policy.resolve(
                trade, qty_remaining, legs, alloc_cost_ccy
            )
            if gap_event is not None:
                gap_fixed = gap_event.fixed

        if gap_event is not None:
            self.recorder.record_gap(gap_event)

        line = build_realized_line(trade, legs, alloc_cost_ccy)
        if has_gap:
            line.has_gap = True
            line.gap_fixed = gap_fixed
        return line
