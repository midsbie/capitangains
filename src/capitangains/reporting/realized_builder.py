from __future__ import annotations

from decimal import Decimal

from .fifo_domain import RealizedLine, SellMatchLeg, TradeProtocol
from .money import abs_decimal, quantize_money
from .trade_math import sell_gross_ccy, sell_net_ccy


def build_realized_line(
    trade: TradeProtocol,
    legs: list[SellMatchLeg],
    alloc_cost_ccy: Decimal,
) -> RealizedLine:
    sell_gross = sell_gross_ccy(trade.proceeds)
    sell_net = sell_net_ccy(trade.proceeds, trade.comm_fee)
    realized_ccy = quantize_money(sell_net - alloc_cost_ccy)
    sell_qty = abs_decimal(trade.quantity)

    return RealizedLine(
        symbol=trade.symbol,
        currency=trade.currency,
        sell_date=trade.date,
        sell_qty=sell_qty,
        sell_gross_ccy=sell_gross,
        sell_comm_ccy=trade.comm_fee,
        sell_net_ccy=sell_net,
        legs=list(legs),
        realized_pl_ccy=realized_ccy,
    )
