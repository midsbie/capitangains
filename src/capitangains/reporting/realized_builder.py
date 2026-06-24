from __future__ import annotations

from .fifo_domain import RealizedLine, SellMatchLeg, TradeProtocol
from .money import abs_decimal
from .trade_math import sell_gross_ccy, sell_net_ccy


def build_realized_line(
    trade: TradeProtocol, legs: list[SellMatchLeg]
) -> RealizedLine:
    """Assemble a RealizedLine from a sell trade and its matched legs.

    Only the trade's own primitives are set; allocated cost and realized P/L are
    RealizedLine properties derived from the legs and the net proceeds, so they cannot
    drift from the legs that back them.
    """
    return RealizedLine(
        symbol=trade.symbol,
        currency=trade.currency,
        sell_date=trade.date,
        sell_qty=abs_decimal(trade.quantity),
        sell_gross_ccy=sell_gross_ccy(trade.proceeds),
        sell_comm_ccy=trade.comm_fee,
        sell_net_ccy=sell_net_ccy(trade.proceeds, trade.comm_fee),
        legs=list(legs),
    )
