from __future__ import annotations

import logging

from .events import EventRecorder
from .fifo_domain import (
    GapEvent,
    GapResolution,
    Lot,
    RealizedLine,
    TradeProtocol,
    TransferProtocol,
    TransferShortfall,
)
from .gap_policy import GapPolicy
from .money import abs_decimal
from .positions import PositionBook
from .realized_builder import build_realized_line
from .trade_math import buy_cost_ccy

logger = logging.getLogger(__name__)


class FifoMatcher:
    def __init__(
        self,
        *,
        gap_policy: GapPolicy,
        positions: PositionBook | None = None,
        recorder: EventRecorder | None = None,
    ) -> None:
        self.positions = positions or PositionBook()
        self.recorder = recorder or EventRecorder()
        self._gap_policy = gap_policy

    @property
    def gap_events(self) -> list[GapEvent]:
        return self.recorder.gap_events

    @property
    def transfer_shortfalls(self) -> list[TransferShortfall]:
        return self.recorder.transfer_shortfalls

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
            # Seed a lot at market_value as its cost basis; the original basis is not
            # reliably carried in the CSV (see the transfers extractor).
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
            qty_to_remove = transfer.quantity
            logger.debug(
                "Processing transfer OUT: %s %s @ %s",
                transfer.quantity,
                transfer.symbol,
                transfer.date,
            )
            try:
                legs, alloc_cost, qty_remaining = self.positions.consume_fifo(
                    transfer.symbol, transfer.currency, qty_to_remove
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
                    # Record the shortfall for the boundary to report; the lots that did
                    # exist are already consumed (above), so a later sell still gaps.
                    self.recorder.record_transfer_shortfall(
                        TransferShortfall(
                            symbol=transfer.symbol,
                            date=transfer.date,
                            requested_qty=qty_to_remove,
                            remaining_qty=qty_remaining,
                            currency=transfer.currency,
                        )
                    )
            except Exception:
                logger.warning(
                    "Failed to process Transfer OUT of %s shares of %s on %s. "
                    "Position tracking may be inaccurate.",
                    transfer.quantity,
                    transfer.symbol,
                    transfer.date,
                )

                # The warning above is informational. Re-raise so an invariant violation
                # from consume_fifo is not swallowed, which would let execution continue
                # on corrupted position state.
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
            "Position book for %s/%s: %d lots, total qty: %s",
            trade.symbol,
            trade.currency,
            self.positions.lot_count(trade.symbol, trade.currency),
            self.positions.total_qty(trade.symbol, trade.currency),
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
            self.positions.lot_count(trade.symbol, trade.currency),
        )

        legs, alloc_cost_ccy, qty_remaining = self.positions.consume_fifo(
            trade.symbol, trade.currency, qty_to_sell
        )

        matched_qty = qty_to_sell - qty_remaining
        logger.debug(
            "FIFO consumed %d leg(s) for %s shares, cost: %s %s",
            len(legs),
            matched_qty,
            alloc_cost_ccy,
            trade.currency,
        )

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
            result = self._gap_policy.resolve(trade, qty_remaining, alloc_cost_ccy)
            legs.append(result.leg)
            alloc_cost_ccy = result.alloc_cost
            gap_event = result.event
            gap_fixed = gap_event.outcome is GapResolution.SYNTHESIZED
            if gap_fixed:
                logger.info(
                    "Gap resolved by policy: added leg with %s shares (cost: %s %s)",
                    result.leg.qty,
                    result.leg.alloc_cost_ccy,
                    trade.currency,
                )
            else:
                # The CLI boundary owns user-facing reporting of unresolved gaps
                # (the two-way acknowledgment tie-out); keep this at debug.
                logger.debug("Gap NOT resolved: %s", gap_event.message)
            self.recorder.record_gap(gap_event)

        line = build_realized_line(trade, legs, alloc_cost_ccy)
        if has_gap:
            line.has_gap = True
            line.gap_fixed = gap_fixed
        return line
