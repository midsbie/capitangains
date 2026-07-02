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
    rate) and proceeds_share_eur its share of the net sale proceeds (at the line's
    sell-date rate). proceeds_gross_share_eur is its share of the gross proceeds and
    comm_share_eur the net - gross residual (a negative), so gross + commission == net
    per leg and Anexo J Quadro 9.2A can file the realization value (Valor de Realizacao)
    and the expenses (Despesas) as separate per-lot fields that still tie to the net.
    buy_rate is the EUR-per-unit rate the acquisition cost was priced at
    (the sell-date rate for a dateless gap leg, which has no acquisition date), kept so
    the filer-facing sheets can show the exact rate behind each EUR figure.
    """

    leg: SellMatchLeg
    alloc_cost_eur: Decimal
    proceeds_share_eur: Decimal
    proceeds_gross_share_eur: Decimal
    comm_share_eur: Decimal
    buy_rate: Decimal


@dataclass(frozen=True)
class ConvertedRealizedLine:
    """A realized line valued in EUR.

    line is the trade-currency source (symbol, currency, dates, quantities, every *_ccy
    figure); the EUR totals are the line's gross/commission/net proceeds, total
    allocated cost, and realized P/L. legs carries the per-leg EUR breakdown the Anexo J
    sheet flattens. sell_rate is the EUR-per-unit rate the sale figures were priced at
    (the sell-date rate), surfaced so the Realized sheet can show it beside its EUR
    block. The Realized sheet reads ccy columns off line and EUR columns off this.
    """

    line: RealizedLine
    legs: list[ConvertedLeg]
    sell_gross_eur: Decimal
    sell_comm_eur: Decimal
    sell_net_eur: Decimal
    alloc_cost_eur: Decimal
    realized_pl_eur: Decimal
    sell_rate: Decimal
