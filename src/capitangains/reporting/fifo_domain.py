from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol types for duck-typed trade/transfer objects
# ---------------------------------------------------------------------------


@runtime_checkable
class TradeProtocol(Protocol):
    """Minimal interface for trade objects used in FIFO matching."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    proceeds: Decimal
    comm_fee: Decimal


@runtime_checkable
class TradeWithBasisProtocol(TradeProtocol, Protocol):
    """Trade protocol extended with optional basis for gap resolution."""

    basis_ccy: Decimal | None


@runtime_checkable
class TransferProtocol(Protocol):
    """Minimal interface for transfer objects used in position seeding."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    direction: str
    market_value: Decimal
    code: str


# ---------------------------------------------------------------------------
# Concrete implementations of protocols
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    """Minimal trade object implementing TradeProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    proceeds: Decimal
    comm_fee: Decimal
    basis_ccy: Decimal | None = None


@dataclass
class Transfer:
    """Minimal transfer object implementing TransferProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    direction: str
    market_value: Decimal
    code: str = ""


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SellMatchLeg:
    buy_date: dt.date | None
    qty: Decimal
    lot_qty_before: Decimal
    alloc_cost_ccy: Decimal
    synthetic: bool = False
    transferred: bool = False
    alloc_cost_eur: Decimal | None = None
    proceeds_share_eur: Decimal | None = None


@dataclass
class Lot:
    buy_date: dt.date
    qty: Decimal  # remaining quantity in lot
    basis_ccy: Decimal  # total basis in trade currency (incl. buy fees)
    currency: str
    transferred: bool = False  # True if lot originated from a transfer


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
