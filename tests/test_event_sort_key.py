import datetime as dt
from decimal import Decimal

from capitangains.cmd.cli import _event_sort_key
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


def test_transfer_in_before_trades_transfer_out_after():
    """Transfer-in < trades < transfer-out on the same date."""
    xfer_in = _transfer(dt.date(2024, 6, 15), "In")
    trade = _trade("2024-06-15, 12:00:00", "100")
    xfer_out = _transfer(dt.date(2024, 6, 15), "Out")

    events: list[TradeRow | TransferRow] = [xfer_out, trade, xfer_in]
    events.sort(key=_event_sort_key)

    assert events == [xfer_in, trade, xfer_out]
