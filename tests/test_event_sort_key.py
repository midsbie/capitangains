import datetime as dt

from capitangains.reporting.event_stream import _event_sort_key
from capitangains.reporting.extract import TradeRow, TransferRow
from tests.support import trade_row, transfer_row


def _trade(datetime_str: str, quantity: str) -> TradeRow:
    return trade_row(
        datetime_str=datetime_str,
        date=datetime_str.split(",")[0],
        quantity=quantity,
    )


def test_intraday_sell_before_buy_preserves_chronological_order():
    """A morning sell must sort before an afternoon buy on the same day."""
    morning_sell = _trade("2024-06-15, 09:30:00", "-100")
    afternoon_buy = _trade("2024-06-15, 15:00:00", "100")

    events = [afternoon_buy, morning_sell]
    events.sort(key=_event_sort_key)

    assert events == [morning_sell, afternoon_buy]


def test_same_timestamp_buy_before_sell():
    """When trades share an identical timestamp, buys should sort before sells."""
    buy = _trade("2024-06-15, 12:00:00", "100")
    sell = _trade("2024-06-15, 12:00:00", "-50")

    events = [sell, buy]
    events.sort(key=_event_sort_key)

    assert events == [buy, sell]


def test_transfers_order_by_date_relative_to_trades():
    """A transfer carries no intraday time, so it sorts on date alone.

    A same-day, same-symbol transfer/trade collision can no longer reach this sort: it
    is rejected upstream by report_ordering_collisions, since IBKR provides no
    transfer timestamp to order it. The only same-day pairings that survive to the sort
    are in independent symbols (immaterial to FIFO), so the key needs no fabricated
    transfer-vs-trade tie-break; date ordering is sufficient.
    """
    prior_xfer = transfer_row(date=dt.date(2024, 6, 14), direction="In")
    day_trade = _trade("2024-06-15, 12:00:00", "100")
    later_xfer = transfer_row(date=dt.date(2024, 6, 16), direction="Out")

    events: list[TradeRow | TransferRow] = [later_xfer, day_trade, prior_xfer]
    events.sort(key=_event_sort_key)

    assert events == [prior_xfer, day_trade, later_xfer]


def test_trades_sort_by_date_across_days():
    """Trades on different days sort ascending by date regardless of input order.

    Migrated from the trades extractor, which no longer pre-sorts; _event_sort_key is
    the single source of ordering truth. The same-date buy-before-sell tie-break and
    intraday time ordering are covered by the two tests above.
    """
    jan = _trade("2024-01-10, 09:00:00", "50")
    feb = _trade("2024-02-20, 12:00:00", "75")
    mar = _trade("2024-03-15, 10:00:00", "100")

    events = [mar, jan, feb]
    events.sort(key=_event_sort_key)

    assert events == [jan, feb, mar]
