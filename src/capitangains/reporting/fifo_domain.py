from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple, Protocol, runtime_checkable

from capitangains.conv import Currency


@runtime_checkable
class TradeProtocol(Protocol):
    """Minimal interface for trade objects used in FIFO matching."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: Currency
    proceeds: Decimal
    comm_fee: Decimal


@runtime_checkable
class TransferProtocol(Protocol):
    """Minimal interface for transfer objects used in position seeding."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: Currency
    direction: str
    market_value: Decimal
    code: str


@dataclass
class SellMatchLeg:
    buy_date: dt.date | None
    qty: Decimal
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
    currency: Currency
    transferred: bool = False  # True if lot originated from a transfer


@dataclass
class RealizedLine:
    symbol: str
    currency: Currency
    sell_date: dt.date
    sell_qty: Decimal  # positive quantity sold (abs of trade negative qty)
    sell_gross_ccy: Decimal  # abs(proceeds) before fees
    sell_comm_ccy: Decimal  # signed (typically negative)
    sell_net_ccy: Decimal  # gross + comm (fees reduce proceeds)
    legs: list[SellMatchLeg]
    realized_pl_ccy: Decimal
    has_gap: bool = False
    gap_fixed: bool = False
    # True when IBKR elided this disposal's per-trade Realized P/L (Forex, or a corrupt
    # cell), so it carries no IBKR figure and is dropped from the cross-check on BOTH
    # sides. Set at event-stream replay; see reconcile.reconcile_realized_against_ibkr.
    ibkr_realized_elided: bool = False
    sell_gross_eur: Decimal | None = None
    sell_comm_eur: Decimal | None = None
    sell_net_eur: Decimal | None = None
    alloc_cost_eur: Decimal | None = None
    realized_pl_eur: Decimal | None = None

    @property
    def alloc_cost_ccy(self) -> Decimal:
        """Total allocated cost in trade currency: the sum of the legs' pieces.

        The trade-currency counterpart to the stored ``alloc_cost_eur``; derived rather
        than stored because the legs already carry the authoritative per-lot pieces.
        """
        return sum((leg.alloc_cost_ccy for leg in self.legs), Decimal("0"))


class GapResolution(enum.Enum):
    """Outcome of resolving an unmatched SELL (a "gap"), partitioned at the boundary.

    A gap is always a real, in-scope disposal that must be reported; the only open
    question is the valuation of its cost basis. These three outcomes encode that
    valuation verdict so the CLI boundary can decide fatality:

    SYNTHESIZED: the gap was acknowledged and its IBKR Basis is usable, so a real
        synthetic cost lot was appended. Non-fatal (emits a per-lot audit warning).
    UNACKNOWLEDGED: the gap was not named in the operator's acknowledgment list. Fatal.
    DEFECTIVE: the gap was acknowledged, but its IBKR Basis is missing or internally
        corrupt (fails Proceeds + Comm + Basis = Realized, or undershoots
        already-matched cost beyond tolerance), so no defensible cost can be
        synthesized. Fatal.
    """

    SYNTHESIZED = enum.auto()
    UNACKNOWLEDGED = enum.auto()
    DEFECTIVE = enum.auto()


# An operator's acknowledgment of one gap, compared directly against (trade.symbol,
# trade.date). The symbol is whitespace-stripped only (case and dots preserved); the
# date is parsed the same way TradeRow.date is, so the two are equality-comparable.
GapKey = tuple[str, dt.date]


@dataclass
class GapEvent:
    symbol: str
    date: dt.date
    remaining_qty: Decimal
    currency: Currency
    message: str
    outcome: GapResolution


class ResolvedGap(NamedTuple):
    """How a gap policy valued one unmatched SELL quantity.

    ``leg`` is the single gap leg to append -- a zero-cost placeholder or a synthetic
    lot; ``alloc_cost`` is the resulting total allocated cost; ``event`` is the audit
    record of the resolution.
    """

    leg: SellMatchLeg
    alloc_cost: Decimal
    event: GapEvent
