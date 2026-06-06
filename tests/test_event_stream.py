"""EventStream: the chronologically ordered trade/transfer union driven into FIFO.

_event_sort_key's ordering rule is covered in test_event_sort_key.py. These tests pin
the value object's two contributions: it sorts at construction (so replay drives the
matcher in chronological order regardless of input order) and replay returns exactly the
realized sell lines, dispatching transfers to position seeding/consumption.
"""

import datetime as dt
from decimal import Decimal

from capitangains.reporting.event_stream import EventStream
from capitangains.reporting.extract import TradeRow, TransferRow
from capitangains.reporting.fifo import FifoMatcher
from capitangains.reporting.gap_policy import build_gap_policy


def _trade(symbol: str, date: str, qty: str, proceeds: str) -> TradeRow:
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency="USD",
        symbol=symbol,
        datetime_str=f"{date}, 12:00:00",
        date=dt.date.fromisoformat(date),
        quantity=Decimal(qty),
        t_price=Decimal("100"),
        proceeds=Decimal(proceeds),
        comm_fee=Decimal("0"),
        code="O",
    )


def _transfer_in(symbol: str, date: str, qty: str, market_value: str) -> TransferRow:
    return TransferRow(
        section="Transfers",
        asset_category="Stocks",
        currency="USD",
        symbol=symbol,
        date=dt.date.fromisoformat(date),
        direction="In",
        quantity=Decimal(qty),
        market_value=Decimal(market_value),
        code="",
    )


def _matcher() -> FifoMatcher:
    return FifoMatcher(gap_policy=build_gap_policy(frozenset()))


def test_replay_sorts_before_driving_so_a_later_input_buy_still_matches():
    """A buy passed after its sell must still seed the lot the sell consumes.

    The buy precedes the sell in time but is listed second; if replay drove the raw
    input order the sell would find no lot and open a gap. A clean match (has_gap False)
    proves the constructor ordered the stream before replay drove it.
    """
    buy = _trade("AAA", "2024-01-01", "100", "-1000")
    sell = _trade("AAA", "2024-06-01", "-100", "1500")

    realized = EventStream(trades=[sell, buy], transfers=[]).replay(_matcher())

    assert len(realized) == 1
    assert realized[0].symbol == "AAA"
    assert realized[0].sell_qty == Decimal("100")
    assert realized[0].has_gap is False


def test_replay_dispatches_transfers_to_position_seeding():
    """A transfer-in seeds the lot a later sell consumes, yielding no realized line."""
    transfer = _transfer_in("BBB", "2024-01-01", "100", "1000")
    sell = _trade("BBB", "2024-06-01", "-100", "1500")

    realized = EventStream(trades=[sell], transfers=[transfer]).replay(_matcher())

    assert len(realized) == 1
    assert realized[0].symbol == "BBB"
    assert realized[0].has_gap is False


def test_replay_returns_no_lines_when_there_are_no_sells():
    """Buys and transfer-ins build positions but realize nothing."""
    buy = _trade("CCC", "2024-01-01", "100", "-1000")
    transfer = _transfer_in("DDD", "2024-01-01", "50", "500")

    realized = EventStream(trades=[buy], transfers=[transfer]).replay(_matcher())

    assert realized == []
