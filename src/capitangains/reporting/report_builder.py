from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, TypeVar

from capitangains.conv import Currency

from .extract import DividendRow, InterestRow, SyepInterestRow, WithholdingRow
from .fifo import RealizedLine
from .fifo_domain import SellMatchLeg, TransferProtocol
from .fx import FxTable
from .money import quantize_money
from .quadro_8a import Quadro8ALine, aggregate_quadro_8a

_RowT = TypeVar("_RowT")


class _ConvertibleAmount(Protocol):
    """A dated, currency-tagged amount the EUR pass prices in place.

    The structural shape shared by DividendRow, InterestRow, and WithholdingRow:
    _convert_amounts reads (currency, date, amount) and writes amount_eur, so the three
    income streams convert through one loop regardless of their other fields.
    """

    currency: Currency
    date: dt.date
    amount: Decimal
    amount_eur: Decimal | None


@dataclass
class CurrencyTotals:
    """Aggregated monetary totals for a single currency."""

    realized: Decimal = field(default_factory=lambda: Decimal("0"))
    proceeds: Decimal = field(default_factory=lambda: Decimal("0"))
    alloc_cost: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class SymbolTotals:
    """Aggregated totals for a symbol across currencies."""

    by_currency: dict[Currency, CurrencyTotals] = field(default_factory=dict)
    eur: CurrencyTotals = field(default_factory=CurrencyTotals)

    def get_currency(self, currency: Currency) -> CurrencyTotals:
        """Get or create currency totals."""
        if currency not in self.by_currency:
            self.by_currency[currency] = CurrencyTotals()

        return self.by_currency[currency]


@dataclass
class ReportBuilder:
    """Accumulate one tax year's reportable rows and aggregates for the sink.

    Scoped to a single ``year``: the bulk ingest methods (``add_realized_lines`` and
    the ``set_*`` setters) retain only rows dated in that year, so a caller may hand
    over an unscoped multi-year parse and trust the builder to keep just its year.
    ``add_realized`` is the unscoped per-line primitive that the ingest path and the
    tests build on.
    """

    year: int
    # Source country for broker-paid interest (which carries no country in the data):
    # the IBKR contracting entity's jurisdiction. Default IE (IBKR Ireland),
    # CLI-overridable via --broker-country.
    broker_country: str = "IE"
    realized_lines: list[RealizedLine] = field(default_factory=list)
    symbol_totals: dict[str, SymbolTotals] = field(default_factory=dict)
    dividends: list[DividendRow] = field(default_factory=list)
    withholding: list[WithholdingRow] = field(default_factory=list)
    syep_interest: list[SyepInterestRow] = field(default_factory=list)
    interest: list[InterestRow] = field(default_factory=list)
    transfers: list[TransferProtocol] = field(default_factory=list)
    # Every (date, currency) lookup no FX rate could satisfy. A complete table is a
    # precondition for the EUR report: the CLI aborts (exit 2) when this is non-empty
    # rather than emit substituted or blank EUR figures.
    fx_missing: set[tuple[dt.date, Currency]] = field(default_factory=set)

    def add_realized(self, rl: RealizedLine) -> None:
        self.realized_lines.append(rl)
        if rl.symbol not in self.symbol_totals:
            self.symbol_totals[rl.symbol] = SymbolTotals()
        t = self.symbol_totals[rl.symbol]
        ccy = t.get_currency(rl.currency)
        ccy.realized += rl.realized_pl_ccy
        ccy.proceeds += rl.sell_net_ccy
        ccy.alloc_cost += rl.alloc_cost_ccy

    def add_realized_lines(self, lines: Iterable[RealizedLine]) -> None:
        """Aggregate every realized line whose sell date falls in this report's year."""
        for line in self._in_year(lines, key=lambda rl: rl.sell_date):
            self.add_realized(line)

    def set_dividends(self, rows: Iterable[DividendRow]) -> None:
        self.dividends = self._in_year(rows, key=lambda r: r.date)

    def set_withholding(self, rows: Iterable[WithholdingRow]) -> None:
        self.withholding = self._in_year(rows, key=lambda r: r.date)

    def set_syep_interest(self, rows: Iterable[SyepInterestRow]) -> None:
        self.syep_interest = self._in_year(rows, key=lambda r: r.value_date)

    def set_interest(self, rows: Iterable[InterestRow]) -> None:
        self.interest = self._in_year(rows, key=lambda r: r.date)

    def set_transfers(self, transfers: Iterable[TransferProtocol]) -> None:
        """Retain only this year's transfers for the Stock Transfers sheet.

        The full multi-file transfer set (prior years included) is needed upstream to
        seed FIFO, but that ingestion is already complete before the report is built;
        transfers here feed only the Stock Transfers sheet, never a computed figure.
        Scoping to the report year is therefore display-only -- it cannot move a tax
        number -- and keeps a prior-year seeding transfer from masquerading as a
        current-year event on a single-year report.
        """
        self.transfers = self._in_year(transfers, key=lambda t: t.date)

    def _in_year(
        self, rows: Iterable[_RowT], key: Callable[[_RowT], dt.date | None]
    ) -> list[_RowT]:
        """Keep only rows whose scoping date falls in this report's ``year``.

        Each row type carries its membership date on a different attribute (realized
        lines: sell_date, cash flows: date, SYEP: an optional value_date), so the
        caller injects the accessor. A None date is out of scope: only SYEP rows can
        lack a value_date, and a dateless row cannot be placed in any year (this also
        drops the CSV 'Total' line the SYEP extractor leaves undated).
        """
        return [r for r in rows if (d := key(r)) is not None and d.year == self.year]

    def convert_eur(self, fx: FxTable | None) -> None:
        """Convert realized lines to EUR using per-date FX if available.

        PT practice: acquisition values -> EUR at buy date; sale values -> EUR at sale
        date.
        """
        self._convert_realized_lines(fx)
        self._convert_syep_interest(fx)
        self._convert_amounts(self.withholding, fx)
        self._convert_amounts(self.dividends, fx)
        self._convert_amounts(self.interest, fx)
        self._recompute_aggregates()

    def _convert_realized_lines(self, fx: FxTable | None) -> None:
        for rl in self.realized_lines:
            self._convert_realized_line(rl, fx)

    def _convert_realized_line(self, rl: RealizedLine, fx: FxTable | None) -> None:
        # PT practice: proceeds convert at the sell-date rate, each acquisition leg at
        # its own buy-date rate. EUR is not special-cased: _rate_or_record returns 1 for
        # the base currency, so an EUR line flows through the same cent-quantizing
        # arithmetic as a converted one (value * 1). That keeps every *_eur field
        # cent-exact for any currency, the per-leg alloc_cost_eur in particular, which
        # feeds the filer-facing Anexo J cost. If ANY required rate is missing the whole
        # line is left unconverted (and the gap recorded); a rate from another date is
        # never substituted, as that would silently misstate cost basis or proceeds.
        sell_rate = self._rate_or_record(rl.sell_date, rl.currency, fx)
        leg_rates = [
            sell_rate
            if leg.buy_date is None
            else self._rate_or_record(leg.buy_date, rl.currency, fx)
            for leg in rl.legs
        ]
        if sell_rate is None or any(rate is None for rate in leg_rates):
            return

        rl.sell_gross_eur = quantize_money(rl.sell_gross_ccy * sell_rate)
        rl.sell_comm_eur = quantize_money(rl.sell_comm_ccy * sell_rate)
        rl.sell_net_eur = quantize_money(rl.sell_net_ccy * sell_rate)

        alloc_eur = Decimal("0")
        for leg, rate in zip(rl.legs, leg_rates, strict=True):
            assert rate is not None  # guaranteed by the guard above; narrows for mypy
            leg.alloc_cost_eur = quantize_money(leg.alloc_cost_ccy * rate)
            alloc_eur += leg.alloc_cost_eur
        rl.alloc_cost_eur = quantize_money(alloc_eur)
        rl.realized_pl_eur = quantize_money(rl.sell_net_eur - rl.alloc_cost_eur)
        self._allocate_proceeds_to_legs(rl.legs, rl.sell_qty, rl.sell_net_eur)

    def _rate_or_record(
        self, date: dt.date, currency: Currency, fx: FxTable | None
    ) -> Decimal | None:
        """Resolve the EUR-per-unit rate for (date, currency); record a miss if absent.

        Returns None -- and accumulates (date, currency) in ``fx_missing`` -- when no
        table is given, the currency is absent, or no rate exists on/before the date.
        Never substitutes another date's rate.
        """
        if currency.is_base:
            return Decimal("1")

        rate = fx.get_rate(date, currency) if fx is not None else None
        if rate is None:
            self.fx_missing.add((date, currency))

        return rate

    @staticmethod
    def _allocate_proceeds_to_legs(
        legs: list[SellMatchLeg],
        sell_qty: Decimal,
        sell_net_eur: Decimal | None,
    ) -> None:
        """Allocate sale proceeds EUR across legs by quantity share, cent-exact.

        Last leg absorbs the rounding residual so the sum equals sell_net_eur.
        """
        if sell_qty == 0 or sell_net_eur is None or not legs:
            return

        allocated = Decimal("0")
        for leg in legs[:-1]:
            leg.proceeds_share_eur = quantize_money(sell_net_eur * leg.qty / sell_qty)
            allocated += leg.proceeds_share_eur

        legs[-1].proceeds_share_eur = sell_net_eur - allocated

    def _convert_syep_interest(self, fx: FxTable | None) -> None:
        if not self.syep_interest:
            return

        for row in self.syep_interest:
            row.interest_paid_eur = self._convert_amount_to_eur(
                row.currency, row.value_date, row.interest_paid, fx
            )

    def _convert_amounts(
        self, rows: Iterable[_ConvertibleAmount], fx: FxTable | None
    ) -> None:
        """Price each dated cash-flow row in EUR, in place.

        A None amount_eur means no rate was available; the gap is recorded by
        _convert_amount_to_eur via _rate_or_record.
        """
        for row in rows:
            row.amount_eur = self._convert_amount_to_eur(
                row.currency, row.date, row.amount, fx
            )

    def _convert_amount_to_eur(
        self,
        currency: Currency,
        date: dt.date | None,
        amount: Decimal,
        fx: FxTable | None,
    ) -> Decimal | None:
        """Convert a single amount to EUR, or None when no rate is available.

        A non-EUR amount with no date cannot be priced; set_syep_interest already drops
        SYEP rows lacking a value date, so this returns None without recording an
        FX-table gap (the absence is a source-data issue, not a missing rate).
        """
        if currency.is_base:
            return quantize_money(amount)

        if date is None:
            return None

        rate = self._rate_or_record(date, currency, fx)
        if rate is None:
            return None

        return quantize_money(amount * rate)

    def _recompute_aggregates(self) -> None:
        # Clear prior EUR aggregates (they would have been zero before conversion)
        for totals in self.symbol_totals.values():
            totals.eur = CurrencyTotals()

        for rl in self.realized_lines:
            if rl.symbol not in self.symbol_totals:
                self.symbol_totals[rl.symbol] = SymbolTotals()

            t = self.symbol_totals[rl.symbol]
            if rl.realized_pl_eur is not None:
                t.eur.realized += rl.realized_pl_eur
            if rl.sell_net_eur is not None:
                t.eur.proceeds += rl.sell_net_eur
            if rl.alloc_cost_eur is not None:
                t.eur.alloc_cost += rl.alloc_cost_eur

    @property
    def quadro_8a(self) -> list[Quadro8ALine]:
        """Anexo J Quadro 8A income lines grouped from the already-converted rows.

        A plain property (not memoized in ``convert_eur``) keeps the data flow explicit
        and free of any coupling to conversion order; the row counts are tiny.
        """
        return aggregate_quadro_8a(
            dividends=self.dividends,
            interest=self.interest,
            withholding=self.withholding,
            broker_country=self.broker_country,
        )
