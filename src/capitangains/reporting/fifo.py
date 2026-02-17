from __future__ import annotations

import logging
from decimal import Decimal

from .events import EventRecorder
from .fifo_domain import GapEvent, Lot, RealizedLine, TradeProtocol, TransferProtocol
from .gap_policy import BasisSynthesisPolicy, GapPolicy, StrictGapPolicy
from .money import abs_decimal
from .positions import PositionBook
from .realized_builder import build_realized_line
from .trade_math import buy_cost_ccy

logger = logging.getLogger(__name__)

_DEFAULT_GAP_TOLERANCE = Decimal("0.02")


def _default_basis_getter(trade: TradeProtocol) -> Decimal | None:
    return getattr(trade, "basis_ccy", None)


class FifoMatcher:
    def __init__(
        self,
        *,
        positions: PositionBook | None = None,
        gap_policy: GapPolicy | None = None,
        recorder: EventRecorder | None = None,
        fix_sell_gaps: bool | None = None,
        gap_tolerance: Decimal | None = None,
    ) -> None:
        self.positions = positions or PositionBook()
        self.recorder = recorder or EventRecorder()

        self.fix_sell_gaps = bool(fix_sell_gaps) if fix_sell_gaps is not None else False
        self.gap_tolerance = (
            gap_tolerance if gap_tolerance is not None else _DEFAULT_GAP_TOLERANCE
        )
        self._gap_policy = self._resolve_gap_policy(gap_policy)

    def _resolve_gap_policy(self, policy: GapPolicy | None) -> GapPolicy:
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

    def ingest_trade(self, trade: TradeProtocol) -> RealizedLine | None:
        qty = trade.quantity
        if qty > 0:
            self._ingest_buy(trade)
            return None
        elif qty < 0:
            return self._ingest_sell(trade)

        raise ValueError("trade quantity cannot be zero")

    def ingest_transfer(self, transfer: TransferProtocol) -> None:
        """Ingest a TransferRow (from extract.py) into the position book.

        Assumes:
        - transfer.direction is 'In' or 'Out' (case-insensitive).
        - transfer.quantity is strictly positive.
        - For 'In', transfer.market_value encodes the lot's cost basis in trade
          currency (used as a proxy for original basis).

        Callers must interleave transfers with trades in chronological order
        to maintain correct FIFO semantics.
        """
        if transfer.quantity <= 0:
            raise ValueError("transfer quantity must be positive")

        direction = transfer.direction.strip().lower()
        if direction == "in":
            # Treat as a buy
            # NOTE: We use market_value as the cost basis. This is an approximation
            # if the true original cost basis is not preserved in the CSV.
            basis = transfer.market_value
            logger.debug(
                "Processing transfer IN: %s %s @ %s (basis: %s %s)",
                transfer.quantity,
                transfer.symbol,
                transfer.date,
                transfer.market_value,
                transfer.currency,
            )
            lot = Lot(
                buy_date=transfer.date,
                qty=transfer.quantity,
                basis_ccy=basis,
                currency=transfer.currency,
                transferred=True,
            )
            self.positions.append_buy(transfer.symbol, lot)
        elif direction == "out":
            # Out transfers: consume from FIFO position book
            qty_to_remove = transfer.quantity
            logger.debug(
                "Processing transfer OUT: %s %s @ %s",
                transfer.quantity,
                transfer.symbol,
                transfer.date,
            )
            try:
                legs, alloc_cost, qty_remaining = self.positions.consume_fifo(
                    transfer.symbol, qty_to_remove
                )
                consumed = qty_to_remove - qty_remaining
                logger.debug(
                    "Transfer OUT consumed %d leg(s) totaling %s shares (cost: %s %s)",
                    len(legs),
                    consumed,
                    alloc_cost,
                    transfer.currency,
                )
                if qty_remaining > 0:
                    logger.warning(
                        "Transfer OUT of %s shares of %s on %s, but only %s shares "
                        "available. Position book may be incomplete.",
                        qty_to_remove,
                        transfer.symbol,
                        transfer.date,
                        qty_to_remove - qty_remaining,
                    )
            except Exception:
                logger.warning(
                    "Failed to process Transfer OUT of %s shares of %s on %s. "
                    "Position tracking may be inaccurate.",
                    transfer.quantity,
                    transfer.symbol,
                    transfer.date,
                )

                # Do NOT silently swallow all errors during transfer OUT processing. If
                # positions.consume_fifo() raises legitimate invariant violations,
                # they're logged as warnings but execution continues—potentially
                # corrupting position state.
                raise
        else:
            raise ValueError(f"Unknown transfer direction: {transfer.direction!r}")

    def _ingest_buy(self, trade: TradeProtocol) -> None:
        if trade.quantity <= 0:
            raise ValueError("buy trades must have positive quantity")
        lot = Lot(
            buy_date=trade.date,
            qty=trade.quantity,
            basis_ccy=buy_cost_ccy(trade.proceeds, trade.comm_fee),
            currency=trade.currency,
        )
        logger.debug(
            "Created position lot: %s %s @ %s (basis: %s %s)",
            lot.qty,
            trade.symbol,
            lot.buy_date,
            lot.basis_ccy,
            trade.currency,
        )
        self.positions.append_buy(trade.symbol, lot)
        logger.debug(
            "Position book for %s: %d lots, total qty: %s",
            trade.symbol,
            len(self.positions._positions[trade.symbol]),
            sum(lp.qty for lp in self.positions._positions[trade.symbol]),
        )
        return None

    def _ingest_sell(self, trade: TradeProtocol) -> RealizedLine:
        if trade.quantity >= 0:
            raise ValueError("sell trades must have negative quantity")
        qty_to_sell = abs_decimal(trade.quantity)

        logger.debug(
            "Processing SELL: %s %s @ %s (available lots: %d)",
            qty_to_sell,
            trade.symbol,
            trade.date,
            len(self.positions._positions.get(trade.symbol, [])),
        )

        legs, alloc_cost_ccy, qty_remaining = self.positions.consume_fifo(
            trade.symbol, qty_to_sell
        )

        matched_qty = qty_to_sell - qty_remaining
        logger.debug(
            "FIFO consumed %d leg(s) for %s shares, cost: %s %s",
            len(legs),
            matched_qty,
            alloc_cost_ccy,
            trade.currency,
        )

        gap_event: GapEvent | None = None
        has_gap = qty_remaining > 0
        gap_fixed = False

        if has_gap:
            logger.info(
                "Gap detected: %s shares unmatched (needed: %s, matched: %s)",
                qty_remaining,
                qty_to_sell,
                matched_qty,
            )
            logger.debug("Invoking gap policy: %s", type(self._gap_policy).__name__)
            legs, alloc_cost_ccy, gap_event = self._gap_policy.resolve(
                trade, qty_remaining, legs, alloc_cost_ccy
            )
            if gap_event is not None:
                gap_fixed = gap_event.fixed
                if gap_fixed:
                    # Find the fix leg (last one added)
                    fix_leg = legs[-1] if legs else None
                    if fix_leg:
                        logger.info(
                            "Gap resolved by policy: added leg with %s shares "
                            "(cost: %s %s)",
                            fix_leg.qty,
                            fix_leg.alloc_cost_ccy,
                            trade.currency,
                        )
                else:
                    logger.warning(
                        "Gap NOT resolved: %s",
                        gap_event.message if gap_event else "unknown reason",
                    )

        if gap_event is not None:
            self.recorder.record_gap(gap_event)

        line = build_realized_line(trade, legs, alloc_cost_ccy)
        if has_gap:
            line.has_gap = True
            line.gap_fixed = gap_fixed
        return line
