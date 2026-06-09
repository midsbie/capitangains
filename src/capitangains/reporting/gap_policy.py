from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol

from .fifo_domain import (
    GapEvent,
    GapKey,
    GapResolution,
    ResolvedGap,
    SellMatchLeg,
    TradeProtocol,
)
from .money import abs_decimal, quantize_allocation

logger = logging.getLogger(__name__)


# IBKR's per-trade columns satisfy Proceeds + Comm + Basis = Realized to sub-cent
# precision (observed residuals <= ~1e-6 on real statements). A residual beyond this
# band means the Basis cell is internally inconsistent with Realized -- a typo or
# corruption -- so a cost synthesized from it would be fabricated, not approximate.
# 0.02 sits well above rounding noise and far below any realistic Basis typo.
_IDENTITY_TOLERANCE = Decimal("0.02")

# Default band for the synthesis residual: when an acknowledged gap's IBKR Basis falls a
# hair below the already-matched cost, a shortfall within this tolerance is rounding
# noise and is clamped to zero rather than rejected. Distinct from the identity check
# above -- that validates the Basis cell; this absorbs sub-cent allocation drift.
_DEFAULT_GAP_TOLERANCE = Decimal("0.02")


def _gap_event(
    trade: TradeProtocol,
    *,
    remaining_qty: Decimal,
    message: str,
    outcome: GapResolution,
) -> GapEvent:
    """Build a GapEvent, lifting the trade's identifying fields into it."""
    return GapEvent(
        symbol=trade.symbol,
        date=trade.date,
        remaining_qty=remaining_qty,
        currency=trade.currency,
        message=message,
        outcome=outcome,
    )


def _zero_cost_leg(qty: Decimal) -> SellMatchLeg:
    """Build a zero-cost placeholder leg for an unmatched quantity."""
    return SellMatchLeg(
        buy_date=None,
        qty=qty,
        lot_qty_before=Decimal("0"),
        alloc_cost_ccy=quantize_allocation(Decimal("0")),
    )


def _zero_cost_resolution(
    trade: TradeProtocol,
    qty_remaining: Decimal,
    alloc_cost_so_far: Decimal,
    *,
    message: str,
    outcome: GapResolution,
) -> ResolvedGap:
    """Resolve a gap at zero cost: placeholder leg, allocation left unchanged.

    Shared by the unacknowledged path (UnacknowledgedGapPolicy) and the defective path
    (BasisSynthesisPolicy); the two differ only in the diagnostic message and the
    partitioning outcome. The placeholder leg keeps a RealizedLine buildable; the CLI
    boundary aborts on a fatal outcome before that line is ever consumed.
    """
    return ResolvedGap(
        leg=_zero_cost_leg(qty_remaining),
        alloc_cost=alloc_cost_so_far,
        event=_gap_event(
            trade, remaining_qty=qty_remaining, message=message, outcome=outcome
        ),
    )


class GapPolicy(Protocol):
    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        alloc_cost_so_far: Decimal,
    ) -> ResolvedGap:  # pragma: no cover - protocol
        ...


class UnacknowledgedGapPolicy:
    """Record an unacknowledged gap; allocate zero cost for the unmatched qty."""

    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        alloc_cost_so_far: Decimal,
    ) -> ResolvedGap:
        message = (
            f"Unacknowledged SELL gap for {trade.symbol} on {trade.date}; "
            f"remaining qty={qty_remaining}."
        )
        return _zero_cost_resolution(
            trade,
            qty_remaining,
            alloc_cost_so_far,
            message=message,
            outcome=GapResolution.UNACKNOWLEDGED,
        )


class BasisSynthesisPolicy:
    """Synthesize a missing cost basis from the trade's IBKR per-trade Basis.

    Pure by construction: it never raises and never logs user-facing errors. Every call
    returns a ResolvedGap whose event outcome the CLI boundary partitions on --
    SYNTHESIZED when the Basis yields a defensible cost, DEFECTIVE when it is missing,
    its own Realized P/L proves it corrupt, or that Realized P/L is elided so the Basis
    cannot be validated. A DEFECTIVE outcome still yields a throwaway zero-cost
    placeholder leg so a RealizedLine can be built, but the boundary aborts before that
    line is ever consumed.
    """

    def __init__(
        self,
        *,
        tolerance: Decimal,
        basis_getter: Callable[[TradeProtocol], Decimal | None],
        realized_getter: Callable[[TradeProtocol], Decimal | None],
    ) -> None:
        self.tolerance = tolerance
        self._basis_getter = basis_getter
        self._realized_getter = realized_getter

    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        alloc_cost_so_far: Decimal,
    ) -> ResolvedGap:
        basis = self._basis_getter(trade)
        logger.debug(
            "Basis for %s sell: %s (source: %s)",
            trade.symbol,
            basis if basis is not None else "None",
            "trade row" if basis is not None else "unavailable",
        )
        if basis is None:
            message = (
                f"Missing IBKR Basis for {trade.symbol} on {trade.date}; "
                f"cannot synthesize a cost for remaining qty={qty_remaining}."
            )
            return self._defective(trade, qty_remaining, alloc_cost_so_far, message)

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
                message = (
                    "Auto-fix guardrail: negative residual alloc for "
                    f"{trade.symbol} on {trade.date}: {residual} "
                    f"(shortfall {abs_residual} exceeds tolerance {self.tolerance})."
                )
                return self._defective(trade, qty_remaining, alloc_cost_so_far, message)

        # Past here we would commit to IBKR's Basis as the cost figure; require IBKR's
        # own Realized P/L to vouch for it first. A Basis that breaks the identity is a
        # typo; an absent Realized leaves the Basis unvalidatable, and since a real IBKR
        # sell carrying a Basis always reports the derived Realized, its absence signals
        # a corrupt row rather than a synthesizable gap. Either way refuse, so an
        # unbounded cost from a bad Basis cannot pass as a synthesized figure.
        rejection = self._basis_rejection_reason(trade, basis)
        if rejection is not None:
            return self._defective(trade, qty_remaining, alloc_cost_so_far, rejection)

        synth_cost = quantize_allocation(residual)
        avg_price = synth_cost / qty_remaining if qty_remaining > 0 else Decimal("0")
        logger.debug(
            "Synthesized basis: %s shares @ %s per share = %s total cost",
            qty_remaining,
            avg_price,
            synth_cost,
        )
        message = (
            "Auto-fixed SELL gap for "
            f"{trade.symbol} on {trade.date}; qty={qty_remaining}, "
            f"alloc={synth_cost} (target={target_alloc})"
        )
        return ResolvedGap(
            leg=SellMatchLeg(
                buy_date=trade.date,
                qty=qty_remaining,
                lot_qty_before=Decimal("0"),
                alloc_cost_ccy=synth_cost,
                synthetic=True,
            ),
            alloc_cost=alloc_cost_so_far + synth_cost,
            event=_gap_event(
                trade,
                remaining_qty=qty_remaining,
                message=message,
                outcome=GapResolution.SYNTHESIZED,
            ),
        )

    @staticmethod
    def _defective(
        trade: TradeProtocol,
        qty_remaining: Decimal,
        alloc_cost_so_far: Decimal,
        message: str,
    ) -> ResolvedGap:
        """Yield a throwaway zero-cost placeholder leg and flag the gap DEFECTIVE.

        The leg keeps the RealizedLine buildable; it is never consumed because the CLI
        boundary aborts on any DEFECTIVE outcome before the report is built.
        """
        return _zero_cost_resolution(
            trade,
            qty_remaining,
            alloc_cost_so_far,
            message=message,
            outcome=GapResolution.DEFECTIVE,
        )

    def _basis_rejection_reason(
        self, trade: TradeProtocol, basis: Decimal
    ) -> str | None:
        """Return why IBKR's Basis cannot be trusted as the cost, or None if it holds.

        Synthesis would adopt IBKR's Basis as the cost, so the Basis must first be
        vouched for by IBKR's own per-trade Realized P/L, whose columns satisfy
        Proceeds + Comm + Basis = Realized to sub-cent precision. Two states reject it:

        - Realized P/L is elided, so nothing bounds a typo'd Basis. This does not occur
          in valid IBKR output, where a sell that carries a Basis always carries the
          derived Realized (IBKR blanks both together, e.g. on Forex legs that have no
          cost basis), so an absent Realized beside a present Basis signals a corrupt or
          column-shifted row, not a synthesizable gap. Refuse rather than fabricate a
          cost from an unverifiable cell (the bound CG-03 lacked: without it a corrupt
          Basis on such a row synthesized an arbitrary cost and only warned).
        - Realized P/L is present and the identity is violated beyond
          _IDENTITY_TOLERANCE, proving the Basis cell corrupt (a typo). The identity,
          not a magnitude factor, distinguishes corruption from a legitimate near-total
          loss: a penny-stock collapse has basis far above proceeds yet still satisfies
          it.

        Returns the rich diagnostic for the caller to attach to a DEFECTIVE outcome; the
        fatal decision itself is made at the CLI boundary.
        """
        realized = self._realized_getter(trade)
        if realized is None:
            return (
                f"IBKR Realized P/L for {trade.symbol} on {trade.date} is elided, so "
                f"its Basis ({basis}) cannot be validated against the "
                f"Proceeds + Comm + Basis = Realized identity. Refusing to synthesize "
                f"a cost basis from an unverifiable cell."
            )

        implied = trade.proceeds + trade.comm_fee + basis
        discrepancy = abs_decimal(implied - realized)
        if discrepancy <= _IDENTITY_TOLERANCE:
            return None

        return (
            f"IBKR Basis ({basis}) for {trade.symbol} on {trade.date} is "
            f"inconsistent with its Realized P/L: Proceeds + Comm + Basis = "
            f"{implied}, but Realized P/L is {realized} (off by {discrepancy}). "
            f"Refusing to synthesize a cost basis from a corrupt cell."
        )


class AcknowledgedGapPolicy:
    """Route each gap by acknowledgment: synthesize the acknowledged, mark the rest.

    A gap whose (symbol, date) the operator has explicitly acknowledged is delegated to
    basis synthesis (which may still find the Basis defective); every other gap is
    delegated to the unacknowledged policy, which records it as UNACKNOWLEDGED. The
    acknowledgment set is the sole router input -- whether an outcome is ultimately
    fatal is decided at the CLI boundary, not here.
    """

    def __init__(
        self,
        *,
        acknowledged: frozenset[GapKey],
        synthesis: BasisSynthesisPolicy,
        unacknowledged: UnacknowledgedGapPolicy,
    ) -> None:
        self._acknowledged = acknowledged
        self._synthesis = synthesis
        self._unacknowledged = unacknowledged

    def resolve(
        self,
        trade: TradeProtocol,
        qty_remaining: Decimal,
        alloc_cost_so_far: Decimal,
    ) -> ResolvedGap:
        if (trade.symbol, trade.date) in self._acknowledged:
            return self._synthesis.resolve(trade, qty_remaining, alloc_cost_so_far)
        return self._unacknowledged.resolve(trade, qty_remaining, alloc_cost_so_far)


def _default_basis_getter(trade: TradeProtocol) -> Decimal | None:
    return getattr(trade, "basis_ccy", None)


def _default_realized_getter(trade: TradeProtocol) -> Decimal | None:
    return getattr(trade, "realized_pl_ccy", None)


def build_gap_policy(
    acknowledged: frozenset[GapKey],
    *,
    tolerance: Decimal = _DEFAULT_GAP_TOLERANCE,
) -> GapPolicy:
    """Assemble the gap policy the report uses, wired to TradeRow's Basis/Realized.

    Acknowledged gaps are synthesized from IBKR's per-trade Basis; every other gap is
    recorded as UNACKNOWLEDGED (the CLI boundary decides fatality). Keeping this single
    composition here leaves FifoMatcher agnostic of the concrete policies, so the CLI --
    the composition root -- injects a ready-made strategy.
    """
    return AcknowledgedGapPolicy(
        acknowledged=acknowledged,
        synthesis=BasisSynthesisPolicy(
            tolerance=tolerance,
            basis_getter=_default_basis_getter,
            realized_getter=_default_realized_getter,
        ),
        unacknowledged=UnacknowledgedGapPolicy(),
    )
