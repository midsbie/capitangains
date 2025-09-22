from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Protocol

from .fifo_domain import GapEvent, SellMatchLeg
from .money import abs_decimal, quantize_allocation


class GapPolicy(Protocol):
    def resolve(
        self,
        trade: Any,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[list[SellMatchLeg], Decimal, GapEvent | None]:  # pragma: no cover - protocol
        ...


class StrictGapPolicy:
    """Record the gap and allocate zero cost for the unmatched quantity."""

    def resolve(
        self,
        trade: Any,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[list[SellMatchLeg], Decimal, GapEvent]:
        message = (
            f"Unmatched SELL for {trade.symbol} on {trade.date}; remaining qty={qty_remaining}."
        )
        self._append_zero_cost_leg(trade, qty_remaining, legs)
        return legs, alloc_cost_so_far, GapEvent(
            symbol=trade.symbol,
            date=trade.date,
            remaining_qty=qty_remaining,
            currency=trade.currency,
            message=message,
            fixed=False,
        )

    @staticmethod
    def _append_zero_cost_leg(trade: Any, qty: Decimal, legs: list[SellMatchLeg]) -> None:
        legs.append(
            {
                "buy_date": None,
                "qty": qty,
                "lot_qty_before": Decimal("0"),
                "alloc_cost_ccy": quantize_allocation(Decimal("0")),
            }
        )


class BasisSynthesisPolicy:
    """Attempt to synthesise missing basis using trade-provided reference data."""

    def __init__(
        self,
        *,
        tolerance: Decimal,
        basis_getter: Callable[[Any], Decimal | None],
    ) -> None:
        self.tolerance = tolerance
        self._basis_getter = basis_getter

    def resolve(
        self,
        trade: Any,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[list[SellMatchLeg], Decimal, GapEvent]:
        basis = self._basis_getter(trade)
        if basis is None:
            message = (
                f"Cannot auto-fix SELL for {trade.symbol} on {trade.date}: missing Basis; remaining qty={qty_remaining}."
            )
            StrictGapPolicy._append_zero_cost_leg(trade, qty_remaining, legs)
            return legs, alloc_cost_so_far, GapEvent(
                symbol=trade.symbol,
                date=trade.date,
                remaining_qty=qty_remaining,
                currency=trade.currency,
                message=message,
                fixed=False,
            )

        target_alloc = abs_decimal(basis)
        residual = quantize_allocation(target_alloc - alloc_cost_so_far)
        if residual < 0:
            if abs_decimal(residual) <= self.tolerance:
                residual = quantize_allocation(Decimal("0"))
            else:
                message = (
                    "Auto-fix guardrail: negative residual alloc for "
                    f"{trade.symbol} on {trade.date}: {residual}. Falling back to zero-cost remainder "
                    f"for qty={qty_remaining}."
                )
                StrictGapPolicy._append_zero_cost_leg(trade, qty_remaining, legs)
                return legs, alloc_cost_so_far, GapEvent(
                    symbol=trade.symbol,
                    date=trade.date,
                    remaining_qty=qty_remaining,
                    currency=trade.currency,
                    message=message,
                    fixed=False,
                )

        synth_cost = quantize_allocation(residual)
        legs.append(
            {
                "buy_date": trade.date,
                "qty": qty_remaining,
                "lot_qty_before": Decimal("0"),
                "alloc_cost_ccy": synth_cost,
                "synthetic": True,
            }
        )
        alloc_cost = alloc_cost_so_far + synth_cost
        message = (
            "Auto-fixed SELL gap for "
            f"{trade.symbol} on {trade.date}; qty={qty_remaining}, alloc={synth_cost} (target={target_alloc})"
        )
        return legs, alloc_cost, GapEvent(
            symbol=trade.symbol,
            date=trade.date,
            remaining_qty=Decimal("0"),
            currency=trade.currency,
            message=message,
            fixed=True,
        )
