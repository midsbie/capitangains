from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple, Protocol, runtime_checkable

from capitangains.conv import Currency

from .money import quantize_money


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
    has_gap: bool = False
    gap_fixed: bool = False
    # True when IBKR elided this disposal's per-trade Realized P/L (Forex, or a corrupt
    # cell), so it carries no IBKR figure and is dropped from the cross-check on BOTH
    # sides. Set at event-stream replay; see reconcile.reconcile_realized_against_ibkr.
    ibkr_realized_elided: bool = False

    @property
    def alloc_cost_ccy(self) -> Decimal:
        """Total allocated cost in trade currency, rounded to the cent.

        The legs carry the authoritative per-lot pieces at allocation (1e-8) precision;
        summing them and rounding once here is the line's single cent boundary for cost
        basis, so the Realized sheet renders one consistent value instead of re-rounding
        a raw sub-cent sum. realized_pl_ccy derives from this rounded cost (not the raw
        sum), which is what makes the sheet's net - alloc == pl cross-foot hold. The EUR
        counterpart (ConvertedRealizedLine.alloc_cost_eur) is built the same way.
        """
        raw = sum((leg.alloc_cost_ccy for leg in self.legs), Decimal("0"))
        return quantize_money(raw)

    @property
    def realized_pl_ccy(self) -> Decimal:
        """Realized P/L in trade currency: net proceeds minus allocated cost.

        Derived, never stored: P/L is definitionally net minus cost, and both operands
        are cent figures, so the difference is exactly the value the sheet shows and
        net - alloc == pl holds by construction. This mirrors realized_pl_eur; storing
        it independently is what let the displayed P/L disagree with net - alloc (F5).
        """
        return quantize_money(self.sell_net_ccy - self.alloc_cost_ccy)


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


@dataclass(frozen=True)
class GapEvent:
    symbol: str
    date: dt.date
    remaining_qty: Decimal
    currency: Currency
    message: str
    outcome: GapResolution


class ResolvedGap(NamedTuple):
    """How a gap policy valued one unmatched SELL quantity.

    leg is the single gap leg to append -- a zero-cost placeholder or a synthetic lot;
    alloc_cost is the resulting total allocated cost; event is the audit record of the
    resolution.
    """

    leg: SellMatchLeg
    alloc_cost: Decimal
    event: GapEvent


@dataclass(frozen=True)
class TransferShortfall:
    """A transfer-OUT the position book could not fully cover.

    Recorded by FifoMatcher when an OUT transfer requests more shares than the symbol's
    lots hold (consume_fifo leaves remaining_qty unmatched). A sibling of GapEvent: the
    engine only records it, and the boundary (diagnostics.report_transfer_shortfalls)
    decides visibility. Carrying remaining_qty (the shortfall) directly, alongside the
    requested_qty, lets the boundary state both the shortfall and the covered amount
    (requested_qty - remaining_qty) without re-deriving either.
    """

    symbol: str
    date: dt.date
    requested_qty: Decimal
    remaining_qty: Decimal
    currency: Currency
