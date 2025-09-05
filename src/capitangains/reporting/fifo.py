from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from capitangains.logging import configure_logging

logger = configure_logging()


@dataclass
class Lot:
    buy_date: dt.date
    qty: Decimal  # remaining quantity in lot
    basis_ccy: Decimal  # total basis in trade currency (incl. buy fees)
    currency: str


@dataclass
class RealizedLine:
    symbol: str
    currency: str
    sell_date: dt.date
    # positive quantity sold (abs of trade negative qty)
    sell_qty: Decimal
    sell_gross_ccy: Decimal  # abs(proceeds) before fees
    sell_comm_ccy: Decimal  # signed (typically negative)
    sell_net_ccy: Decimal  # gross + comm (fees reduce proceeds)
    # Lot matches (for audit trail / Annex G helper)
    # each: {buy_date, qty, alloc_cost_ccy, buy_comm_ccy?, ...}
    legs: list[dict[str, Any]]
    realized_pl_ccy: Decimal

    # EUR conversions (optional, if FX available)
    sell_gross_eur: Optional[Decimal] = None
    sell_comm_eur: Optional[Decimal] = None
    sell_net_eur: Optional[Decimal] = None
    alloc_cost_eur: Optional[Decimal] = None
    realized_pl_eur: Optional[Decimal] = None


class FifoMatcher:
    def __init__(self):
        # positions[symbol] = deque[Lot]
        self.positions: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def _buy_cost_ccy(tr) -> Decimal:
        # Buy cash outflow = -proceeds - comm_fee  (both signed)
        # Example: proceeds = -1000, comm = -1  => 1001
        return (-tr.proceeds) - tr.comm_fee

    @staticmethod
    def _sell_gross_ccy(tr) -> Decimal:
        # Sell gross cash inflow (before fees): proceeds is positive
        return tr.proceeds.copy_abs()

    @staticmethod
    def _sell_net_ccy(tr) -> Decimal:
        # Net proceeds after fees: proceeds + comm_fee (comm_fee negative reduces)
        return tr.proceeds + tr.comm_fee

    def ingest(self, trade) -> Optional[RealizedLine]:
        if trade.quantity > 0:
            # BUY
            lot = Lot(
                buy_date=trade.date,
                qty=trade.quantity,
                basis_ccy=self._buy_cost_ccy(trade),
                currency=trade.currency,
            )
            self.positions[trade.symbol].append(lot)
            return None
        else:
            # SELL
            qty_to_sell = -trade.quantity  # positive
            legs: list[dict[str, Any]] = []
            alloc_cost_ccy = Decimal("0")

            while qty_to_sell > 0 and self.positions[trade.symbol]:
                lot = self.positions[trade.symbol][0]
                take = min(qty_to_sell, lot.qty)
                # proportional cost from this lot
                ratio = (take / lot.qty) if lot.qty != 0 else Decimal("0")
                cost_piece = (lot.basis_ccy * ratio).quantize(Decimal("0.00000001"))
                alloc_cost_ccy += cost_piece
                legs.append(
                    {
                        "buy_date": lot.buy_date,
                        "qty": take,
                        "lot_qty_before": lot.qty,
                        "alloc_cost_ccy": cost_piece,
                    }
                )
                # reduce lot
                lot.qty -= take
                lot.basis_ccy -= cost_piece
                qty_to_sell -= take
                if lot.qty == 0:
                    self.positions[trade.symbol].popleft()

            if qty_to_sell > 0:
                # short sell or not enough lots
                logger.warning(
                    "Not enough lots for %s on %s; remaining qty=%s. Treating remainder as zero-cost.",
                    trade.symbol,
                    trade.date,
                    qty_to_sell,
                )
                # Treat remainder as zero-cost to avoid crash
                legs.append(
                    {
                        "buy_date": None,
                        "qty": qty_to_sell,
                        "lot_qty_before": Decimal("0"),
                        "alloc_cost_ccy": Decimal("0"),
                    }
                )
                alloc_cost_ccy += Decimal("0")
                qty_to_sell = Decimal("0")

            sell_gross = self._sell_gross_ccy(trade)
            sell_net = self._sell_net_ccy(trade)
            realized_ccy = (sell_net - alloc_cost_ccy).quantize(Decimal("0.01"))

            return RealizedLine(
                symbol=trade.symbol,
                currency=trade.currency,
                sell_date=trade.date,
                sell_qty=(-trade.quantity),
                sell_gross_ccy=sell_gross,
                sell_comm_ccy=trade.comm_fee,
                sell_net_ccy=sell_net,
                legs=legs,
                realized_pl_ccy=realized_ccy,
            )

