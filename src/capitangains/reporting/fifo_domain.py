from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

try:
    from typing import NotRequired
except ImportError:  # pragma: no cover
    from typing_extensions import NotRequired  # type: ignore


class SellMatchLeg(TypedDict, total=False):
    buy_date: dt.date | None
    qty: Decimal
    lot_qty_before: Decimal
    alloc_cost_ccy: Decimal
    synthetic: NotRequired[bool]
    alloc_cost_eur: NotRequired[Decimal]
    proceeds_share_eur: NotRequired[Decimal]


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
    sell_qty: Decimal  # positive quantity sold (abs of trade negative qty)
    sell_gross_ccy: Decimal  # abs(proceeds) before fees
    sell_comm_ccy: Decimal  # signed (typically negative)
    sell_net_ccy: Decimal  # gross + comm (fees reduce proceeds)
    legs: list[SellMatchLeg]
    realized_pl_ccy: Decimal
    has_gap: bool = False
    gap_fixed: bool = False
    sell_gross_eur: Decimal | None = None
    sell_comm_eur: Decimal | None = None
    sell_net_eur: Decimal | None = None
    alloc_cost_eur: Decimal | None = None
    realized_pl_eur: Decimal | None = None


@dataclass
class GapEvent:
    symbol: str
    date: dt.date
    remaining_qty: Decimal
    currency: str
    message: str
    fixed: bool
