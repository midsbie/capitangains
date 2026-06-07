"""EventStream: the chronologically ordered trade/transfer union driven into FIFO.

_event_sort_key's ordering rule is covered in test_event_sort_key.py. These tests pin
the value object's two contributions: it sorts at construction (so replay drives the
matcher in chronological order regardless of input order) and replay returns exactly the
realized sell lines, dispatching transfers to position seeding/consumption.
"""

from decimal import Decimal

from capitangains.reporting.event_stream import EventStream
from capitangains.reporting.extract import TradeRow
from tests.support import make_matcher, trade_row, transfer_row


def _trade(symbol: str, date: str, qty: str, proceeds: str) -> TradeRow:
    return trade_row(
        symbol=symbol,
        date=date,
        datetime_str=f"{date}, 12:00:00",
        quantity=qty,
        proceeds=proceeds,
        comm_fee="0",
    )


def test_replay_sorts_before_driving_so_a_later_input_buy_still_matches():
    """A buy passed after its sell must still seed the lot the sell consumes.

    The buy precedes the sell in time but is listed second; if replay drove the raw
    input order the sell would find no lot and open a gap. A clean match (has_gap False)
    proves the constructor ordered the stream before replay drove it.
    """
    buy = _trade("AAA", "2024-01-01", "100", "-1000")
    sell = _trade("AAA", "2024-06-01", "-100", "1500")

    realized = EventStream(trades=[sell, buy], transfers=[]).replay(make_matcher())

    assert len(realized) == 1
    assert realized[0].symbol == "AAA"
    assert realized[0].sell_qty == Decimal("100")
    assert realized[0].has_gap is False


def test_replay_dispatches_transfers_to_position_seeding():
    """A transfer-in seeds the lot a later sell consumes, yielding no realized line."""
    transfer = transfer_row(
        symbol="BBB", date="2024-01-01", quantity="100", market_value="1000"
    )
    sell = _trade("BBB", "2024-06-01", "-100", "1500")

    realized = EventStream(trades=[sell], transfers=[transfer]).replay(make_matcher())

    assert len(realized) == 1
    assert realized[0].symbol == "BBB"
    assert realized[0].has_gap is False


def test_replay_returns_no_lines_when_there_are_no_sells():
    """Buys and transfer-ins build positions but realize nothing."""
    buy = _trade("CCC", "2024-01-01", "100", "-1000")
    transfer = transfer_row(
        symbol="DDD", date="2024-01-01", quantity="50", market_value="500"
    )

    realized = EventStream(trades=[buy], transfers=[transfer]).replay(make_matcher())

    assert realized == []
