from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from .fifo_domain import GapEvent, SellMatchLeg, TradeProtocol
from .money import abs_decimal, quantize_allocation

logger = logging.getLogger(__name__)


class GapPolicy(Protocol):
    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[
        list[SellMatchLeg], Decimal, GapEvent | None
    ]:  # pragma: no cover - protocol
        ...


class StrictGapPolicy:
    """Record the gap and allocate zero cost for the unmatched quantity."""

    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[list[SellMatchLeg], Decimal, GapEvent]:
        message = (
            f"Unmatched SELL for {trade.symbol} on {trade.date}; "
            f"remaining qty={qty_remaining}."
        )
        self._append_zero_cost_leg(trade, qty_remaining, legs)
        return (
            legs,
            alloc_cost_so_far,
            GapEvent(
                symbol=trade.symbol,
                date=trade.date,
                remaining_qty=qty_remaining,
                currency=trade.currency,
                message=message,
                fixed=False,
            ),
        )

    @staticmethod
    def _append_zero_cost_leg(
        trade: TradeProtocol, qty: Decimal, legs: list[SellMatchLeg]
    ) -> None:
        legs.append(
            SellMatchLeg(
                buy_date=None,
                qty=qty,
                lot_qty_before=Decimal("0"),
                alloc_cost_ccy=quantize_allocation(Decimal("0")),
            )
        )


class BasisSynthesisPolicy:
    """Attempt to synthesise missing basis using trade-provided reference data."""

    def __init__(
        self,
        *,
        tolerance: Decimal,
        basis_getter: Callable[[TradeProtocol], Decimal | None],
    ) -> None:
        self.tolerance = tolerance
        self._basis_getter = basis_getter

    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        legs: list[SellMatchLeg],
        alloc_cost_so_far: Decimal,
    ) -> tuple[list[SellMatchLeg], Decimal, GapEvent]:
        basis = self._basis_getter(trade)
        logger.debug(
            "Basis for %s sell: %s (source: %s)",
            trade.symbol,
            basis if basis is not None else "None",
            "trade row" if basis is not None else "unavailable",
        )
        if basis is None:
            message = (
                f"Cannot auto-fix SELL for {trade.symbol} on {trade.date}: "
                f"missing Basis; remaining qty={qty_remaining}."
            )
            logger.debug(
                "Basis synthesis failed: no basis available, using zero-cost leg"
            )
            StrictGapPolicy._append_zero_cost_leg(trade, qty_remaining, legs)
            return (
                legs,
                alloc_cost_so_far,
                GapEvent(
                    symbol=trade.symbol,
                    date=trade.date,
                    remaining_qty=qty_remaining,
                    currency=trade.currency,
                    message=message,
                    fixed=False,
                ),
            )

        target_alloc = abs_decimal(basis)
        residual = quantize_allocation(target_alloc - alloc_cost_so_far)
        logger.debug(
            "Residual calculation: basis=%s, matched_cost=%s, residual=%s "
            "(tolerance: %s)",
            basis,
            alloc_cost_so_far,
            residual,
            self.tolerance,
        )
        if residual < 0:
            abs_residual = abs_decimal(residual)
            if abs_residual <= self.tolerance:
                logger.debug(
                    "Residual passes tolerance check: %s <= %s: rounding to zero",
                    abs_residual,
                    self.tolerance,
                )
                residual = quantize_allocation(Decimal("0"))
            else:
                logger.debug(
                    "Guardrail violation: residual %s is negative and exceeds "
                    "tolerance (%s > %s), cannot synthesize",
                    residual,
                    abs_residual,
                    self.tolerance,
                )
                message = (
                    "Auto-fix guardrail: negative residual alloc for "
                    f"{trade.symbol} on {trade.date}: {residual}. "
                    f"Falling back to zero-cost remainder for qty={qty_remaining}."
                )
                logger.debug("Basis synthesis failed guardrails, using zero-cost leg")
                StrictGapPolicy._append_zero_cost_leg(trade, qty_remaining, legs)
                return (
                    legs,
                    alloc_cost_so_far,
                    GapEvent(
                        symbol=trade.symbol,
                        date=trade.date,
                        remaining_qty=qty_remaining,
                        currency=trade.currency,
                        message=message,
                        fixed=False,
                    ),
                )

        synth_cost = quantize_allocation(residual)
        avg_price = synth_cost / qty_remaining if qty_remaining > 0 else Decimal("0")
        logger.debug(
            "Synthesized basis: %s shares @ %s per share = %s total cost",
            qty_remaining,
            avg_price,
            synth_cost,
        )
        legs.append(
            SellMatchLeg(
                buy_date=trade.date,
                qty=qty_remaining,
                lot_qty_before=Decimal("0"),
                alloc_cost_ccy=synth_cost,
                synthetic=True,
            )
        )
        alloc_cost = alloc_cost_so_far + synth_cost
        message = (
            "Auto-fixed SELL gap for "
            f"{trade.symbol} on {trade.date}; qty={qty_remaining}, "
            f"alloc={synth_cost} (target={target_alloc})"
        )
        return (
            legs,
            alloc_cost,
            GapEvent(
                symbol=trade.symbol,
                date=trade.date,
                remaining_qty=Decimal("0"),
                currency=trade.currency,
                message=message,
                fixed=True,
            ),
        )
