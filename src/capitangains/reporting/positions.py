from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal

from .fifo_domain import Lot, SellMatchLeg
from .money import abs_decimal, quantize_allocation, round_cost_piece


class PositionBook:
    """Maintain FIFO lots per symbol without matching policy concerns."""

    def __init__(self) -> None:
        self._positions: dict[tuple[str, str], deque[Lot]] = defaultdict(deque)

    def append_buy(self, symbol: str, lot: Lot) -> None:
        if lot.qty <= 0:
            raise ValueError("buy lot quantity must be positive")
        self._positions[(symbol, lot.currency)].append(lot)

    def consume_fifo(
        self, symbol: str, currency: str, qty: Decimal
    ) -> tuple[list[SellMatchLeg], Decimal, Decimal]:
        if qty <= 0:
            raise ValueError("qty to consume must be positive")

        legs: list[SellMatchLeg] = []
        alloc_cost_ccy = Decimal("0")
        qty_remaining = qty

        lots = self._positions.get((symbol, currency))
        if not lots:
            return [], Decimal("0"), qty
        while qty_remaining > 0 and lots:
            lot = lots[0]
            take = min(qty_remaining, lot.qty)
            cost_piece = round_cost_piece(lot.basis_ccy, take, lot.qty)
            leg = SellMatchLeg(
                buy_date=lot.buy_date,
                qty=take,
                lot_qty_before=lot.qty,
                alloc_cost_ccy=cost_piece,
                transferred=lot.transferred,
            )
            legs.append(leg)
            alloc_cost_ccy += cost_piece

            lot.qty -= take
            remaining_basis = lot.basis_ccy - cost_piece
            if remaining_basis < 0 and abs_decimal(
                remaining_basis
            ) <= quantize_allocation(Decimal("0.00000001")):
                lot.basis_ccy = quantize_allocation(Decimal("0"))
            else:
                lot.basis_ccy = remaining_basis
            qty_remaining -= take

            if lot.qty <= 0:
                if lot.qty < 0:
                    raise ValueError("lot quantity cannot become negative")
                lots.popleft()

        return legs, alloc_cost_ccy, qty_remaining

    def lot_count(self, symbol: str, currency: str) -> int:
        lots = self._positions.get((symbol, currency))
        return len(lots) if lots else 0

    def total_qty(self, symbol: str, currency: str) -> Decimal:
        lots = self._positions.get((symbol, currency))
        if not lots:
            return Decimal("0")
        return sum((lot.qty for lot in lots), Decimal("0"))

    def has_position(self, symbol: str, currency: str) -> bool:
        key = (symbol, currency)
        return key in self._positions and bool(self._positions[key])
