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

    Trades sort by their real intraday timestamp (IBKR's Date/Time), with buys before
    sells only as a tie-break for an identical timestamp. Transfers carry no intraday
    time, so they sort by date alone. That is sufficient because
    _report_transfer_ordering_collisions has already aborted the run if any transfer
    shared a (symbol, currency) day with other order-sensitive activity. The only
    same-day pairings that can still reach this sort are in independent symbols, whose
    relative order does not affect FIFO (consumption is keyed per symbol).
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

    Upstream precondition (the caller's): same-day same-symbol transfer/trade collisions
    must already be rejected (cli._report_transfer_ordering_collisions). That guarantee
    makes ordering transfers by date alone against intraday-stamped trades sound;
    _event_sort_key carries the full rationale.
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
                realized.append(line)
        return realized
