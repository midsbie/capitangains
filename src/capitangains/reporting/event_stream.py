from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from .extract import TradeRow, TransferRow
from .fifo import FifoMatcher
from .fifo_domain import RealizedLine


def _event_sort_key(
    event: TradeRow | TransferRow,
) -> tuple[dt.date, str, int]:
    """Order the merged trade/transfer stream for FIFO ingestion.

    Trades sort by IBKR's Date/Time, with buys before sells only as a tie-break for an
    identical timestamp; transfers carry no intraday time, so they sort by date alone.
    That is sound because report_ordering_collisions has already aborted the run if any
    untimed event, such as a transfer, or a trade with a date-only Date/Time, shared a
    (symbol, currency) day with other order-sensitive activity. The only same-day
    pairings that can still reach this sort are either fully timestamped (ordered by
    their times) or in independent symbols, whose relative order does not affect FIFO
    (consumption is keyed per symbol).
    """
    if isinstance(event, TransferRow):
        return (event.date, "", 0)
    elif isinstance(event, TradeRow):
        sub = 0 if event.quantity > 0 else 1
        return (event.date, event.datetime_str, sub)
    raise ValueError(f"unexpected event type: {type(event)}")


class EventStream:
    """The chronologically ordered union of a statement's trades and transfers.

    FifoMatcher.ingest_trade / ingest_transfer are order-sensitive -- the sequence in
    which buys, sells, transfer-ins and transfer-outs arrive decides which lots a
    disposal consumes, and therefore its cost basis -- but the matcher does not itself
    enforce an order; it documents the precondition and trusts the caller. This type
    owns that order: sorting happens once, at construction, so an instance cannot exist
    unordered. The matcher's "callers must interleave chronologically" contract thus
    becomes an invariant of the type rather than a rule each call site must remember.

    The ordering knowledge lives here, not on FifoMatcher, on purpose. It needs
    TradeRow.datetime_str (the intraday timestamp), a field the matcher's TradeProtocol
    deliberately omits because matching never uses it -- only ordering does. Binding to
    the concrete extract rows here keeps the matcher decoupled from the extract layer.

    Upstream precondition (the caller's): same-day same-symbol ordering collisions must
    already be rejected (diagnostics.report_ordering_collisions) -- an untimed transfer
    or a date-only trade sharing a day with other same-symbol activity. That guarantee
    makes ordering by date/timestamp alone sound; _event_sort_key carries the full
    rationale.
    """

    def __init__(
        self,
        trades: Sequence[TradeRow],
        transfers: Sequence[TransferRow],
    ) -> None:
        self._events: list[TradeRow | TransferRow] = sorted(
            [*trades, *transfers], key=_event_sort_key
        )

    def replay(self, matcher: FifoMatcher) -> list[RealizedLine]:
        """Drive the matcher over the ordered stream; return only the realized sells.

        Transfers seed or consume position lots and yield no line; a buy yields none; a
        sell yields exactly one RealizedLine. Gap outcomes are a side effect recorded on
        matcher.gap_events, which the boundary reads afterward for its acknowledgment
        tie-out.
        """
        realized: list[RealizedLine] = []
        for event in self._events:
            if isinstance(event, TransferRow):
                matcher.ingest_transfer(event)
            elif (line := matcher.ingest_trade(event)) is not None:
                # event is narrowed to TradeRow here (the transfer case took the branch
                # above). Carry whether IBKR elided this sell's Realized P/L onto the
                # line so the reconciler can drop an elided disposal per-trade, not the
                # whole symbol. Set here rather than in the matcher because the
                # matcher's TradeProtocol deliberately omits the Realized field.
                line.ibkr_realized_elided = event.realized_pl_ccy is None
                realized.append(line)
        return realized
