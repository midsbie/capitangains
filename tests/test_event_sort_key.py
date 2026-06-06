import datetime as dt
from decimal import Decimal

from capitangains.reporting.event_stream import _event_sort_key
from capitangains.reporting.extract import TradeRow, TransferRow


def _trade(datetime_str: str, quantity: str) -> TradeRow:
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency="USD",
        symbol="AAPL",
        datetime_str=datetime_str,
        date=dt.date.fromisoformat(datetime_str.split(",")[0]),
        quantity=Decimal(quantity),
        t_price=Decimal("100"),
        proceeds=Decimal("1000") if Decimal(quantity) < 0 else Decimal("-1000"),
        comm_fee=Decimal("-1"),
        code="O",
    )


def _transfer(date: dt.date, direction: str) -> TransferRow:
    return TransferRow(
        section="Transfers",
        asset_category="Stocks",
        currency="USD",
        symbol="AAPL",
        date=date,
        direction=direction,
        quantity=Decimal("100"),
        market_value=Decimal("10000"),
        code="",
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
    is rejected upstream by report_transfer_ordering_collisions, since IBKR provides no
    transfer timestamp to order it. The only same-day pairings that survive to the sort
    are in independent symbols (immaterial to FIFO), so the key needs no fabricated
    transfer-vs-trade tie-break -- date ordering is sufficient.
    """
    prior_xfer = _transfer(dt.date(2024, 6, 14), "In")
    day_trade = _trade("2024-06-15, 12:00:00", "100")
    later_xfer = _transfer(dt.date(2024, 6, 16), "Out")

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
