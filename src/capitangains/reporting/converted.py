"""EUR-converted views of the realized lines, produced by ReportBuilder.convert_eur.

The FIFO engine emits pure trade-currency domain objects (RealizedLine, SellMatchLeg).
The FX pass prices each line in EUR and yields these wrappers rather than mutating the
domain objects in place: a converted view holds a reference to its source line/leg plus
the non-optional EUR amounts. Wrapping (not copying) keeps the RealizedLine the single
source of every trade-currency field, which the Realized sheet renders side by side with
the EUR columns off one object.

Every EUR field is non-optional: a line is converted only when all its required rates
are present, and the pipeline aborts on any missing rate before the sink, so a written
workbook's converted_lines are always 1:1 with its realized_lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .fifo_domain import RealizedLine, SellMatchLeg


@dataclass(frozen=True)
class ConvertedLeg:
    """One acquisition leg of a realized line, valued in EUR.

    leg is the trade-currency source (buy_date, qty, alloc_cost_ccy, the transferred and
    synthetic flags); alloc_cost_eur is its allocated cost (at the leg's own buy-date
    rate) and proceeds_share_eur its share of the sale proceeds (at the line's sell-date
    rate).
    """

    leg: SellMatchLeg
    alloc_cost_eur: Decimal
    proceeds_share_eur: Decimal


@dataclass(frozen=True)
class ConvertedRealizedLine:
    """A realized line valued in EUR.

    line is the trade-currency source (symbol, currency, dates, quantities, every *_ccy
    figure); the EUR totals are the line's gross/commission/net proceeds, total
    allocated cost, and realized P/L. legs carries the per-leg EUR breakdown the Anexo J
    sheet flattens. The Realized sheet reads ccy columns off line and EUR columns off
    this.
    """

    line: RealizedLine
    legs: list[ConvertedLeg]
    sell_gross_eur: Decimal
    sell_comm_eur: Decimal
    sell_net_eur: Decimal
    alloc_cost_eur: Decimal
    realized_pl_eur: Decimal
