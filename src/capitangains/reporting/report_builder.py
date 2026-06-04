from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .extract import DividendRow, InterestRow, SyepInterestRow, WithholdingRow
from .fifo import RealizedLine
from .fifo_domain import SellMatchLeg, TransferProtocol
from .fx import FxTable
from .money import quantize_money


@dataclass
class CurrencyTotals:
    """Aggregated monetary totals for a single currency."""

    realized: Decimal = field(default_factory=lambda: Decimal("0"))
    proceeds: Decimal = field(default_factory=lambda: Decimal("0"))
    alloc_cost: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class SymbolTotals:
    """Aggregated totals for a symbol across currencies."""

    by_currency: dict[str, CurrencyTotals] = field(default_factory=dict)
    eur: CurrencyTotals = field(default_factory=CurrencyTotals)

    def get_currency(self, currency: str) -> CurrencyTotals:
        """Get or create currency totals."""
        if currency not in self.by_currency:
            self.by_currency[currency] = CurrencyTotals()
        return self.by_currency[currency]


@dataclass
class ReportBuilder:
    year: int
    # Collections
    realized_lines: list[RealizedLine] = field(default_factory=list)
    symbol_totals: dict[str, SymbolTotals] = field(default_factory=dict)
    dividends: list[DividendRow] = field(default_factory=list)
    withholding: list[WithholdingRow] = field(default_factory=list)
    syep_interest: list[SyepInterestRow] = field(default_factory=list)
    interest: list[InterestRow] = field(default_factory=list)
    transfers: list[TransferProtocol] = field(default_factory=list)
    # Every (date, currency) lookup no FX rate could satisfy. A complete table is a
    # precondition for the EUR report: the CLI aborts (exit 2) when this is non-empty
    # rather than emit substituted or blank EUR figures (findings #2 and #3).
    fx_missing: set[tuple[dt.date, str]] = field(default_factory=set)

    def add_realized(self, rl: RealizedLine) -> None:
        self.realized_lines.append(rl)
        # aggregate per symbol
        if rl.symbol not in self.symbol_totals:
            self.symbol_totals[rl.symbol] = SymbolTotals()
        t = self.symbol_totals[rl.symbol]
        ccy = t.get_currency(rl.currency)
        ccy.realized += rl.realized_pl_ccy
        ccy.proceeds += rl.sell_net_ccy
        ccy.alloc_cost += sum((leg.alloc_cost_ccy for leg in rl.legs), Decimal("0"))
        # EUR aggregations if present
        if rl.realized_pl_eur is not None:
            t.eur.realized += rl.realized_pl_eur
            t.eur.proceeds += rl.sell_net_eur or Decimal("0")
            t.eur.alloc_cost += rl.alloc_cost_eur or Decimal("0")

    def set_dividends(self, rows: list[DividendRow]) -> None:
        self.dividends = rows

    def set_withholding(self, rows: list[WithholdingRow]) -> None:
        self.withholding = rows

    def set_syep_interest(self, rows: list[SyepInterestRow]) -> None:
        self.syep_interest = rows

    def set_interest(self, rows: list[InterestRow]) -> None:
        self.interest = rows

    def set_transfers(self, transfers: Sequence[TransferProtocol]) -> None:
        self.transfers = list(transfers)

    def convert_eur(self, fx: FxTable | None) -> None:
        """Convert realized lines to EUR using per-date FX if available.

        PT practice: acquisition values -> EUR at buy date; sale values -> EUR at sale
        date.
        """
        self._convert_realized_lines(fx)
        self._convert_syep_interest(fx)
        self._convert_withholding(fx)
        self._convert_dividends(fx)
        self._convert_interest(fx)
        self._recompute_aggregates()

    def _convert_realized_lines(self, fx: FxTable | None) -> None:
        for rl in self.realized_lines:
            if rl.currency == "EUR":
                self._convert_realized_line_eur(rl)
            else:
                self._convert_realized_line_fx(rl, fx)

    def _convert_realized_line_eur(self, rl: RealizedLine) -> None:
        rl.sell_gross_eur = rl.sell_gross_ccy
        rl.sell_comm_eur = rl.sell_comm_ccy
        rl.sell_net_eur = rl.sell_net_ccy
        alloc_eur = Decimal("0")
        # per-leg EUR breakdown (identity conversion)
        for leg in rl.legs:
            leg.alloc_cost_eur = leg.alloc_cost_ccy
            alloc_eur += leg.alloc_cost_eur
        rl.alloc_cost_eur = quantize_money(alloc_eur)
        rl.realized_pl_eur = quantize_money(rl.sell_net_eur - rl.alloc_cost_eur)
        self._allocate_proceeds_to_legs(rl.legs, rl.sell_qty, rl.sell_net_eur)

    def _convert_realized_line_fx(self, rl: RealizedLine, fx: FxTable | None) -> None:
        # PT practice: proceeds convert at the sell-date rate, each acquisition leg at
        # its own buy-date rate. If ANY required rate is missing the whole line is left
        # unconverted (and the gap recorded); a rate from another date is never
        # substituted, as that would silently misstate cost basis or proceeds.
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
        self, date: dt.date, currency: str, fx: FxTable | None
    ) -> Decimal | None:
        """Resolve the EUR-per-unit rate for (date, currency); record a miss if absent.

        Returns None -- and accumulates (date, currency) in ``fx_missing`` -- when no
        table is given, the currency is absent, or no rate exists on/before the date.
        Never substitutes another date's rate (finding #2).
        """
        cur = currency.upper()
        if cur == "EUR":
            return Decimal("1")
        rate = fx.get_rate(date, cur) if fx is not None else None
        if rate is None:
            self.fx_missing.add((date, cur))
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

    def _convert_withholding(self, fx: FxTable | None) -> None:
        if not self.withholding:
            return
        for row in self.withholding:
            row.amount_eur = self._convert_amount_to_eur(
                row.currency, row.date, row.amount, fx
            )

    def _convert_dividends(self, fx: FxTable | None) -> None:
        if not self.dividends:
            return
        for row in self.dividends:
            row.amount_eur = self._convert_amount_to_eur(
                row.currency, row.date, row.amount, fx
            )

    def _convert_interest(self, fx: FxTable | None) -> None:
        if not self.interest:
            return
        for row in self.interest:
            row.amount_eur = self._convert_amount_to_eur(
                row.currency, row.date, row.amount, fx
            )

    def _convert_amount_to_eur(
        self,
        currency: str,
        date: dt.date | None,
        amount: Decimal,
        fx: FxTable | None,
    ) -> Decimal | None:
        """Convert a single amount to EUR, or None when no rate is available.

        A non-EUR amount with no date cannot be priced; the CLI's year filter already
        drops SYEP rows lacking a value date, so this returns None without recording an
        FX-table gap (the absence is a source-data issue, not a missing rate).
        """
        if currency.upper() == "EUR":
            return quantize_money(amount)
        if date is None:
            return None
        rate = self._rate_or_record(date, currency, fx)
        if rate is None:
            return None
        return quantize_money(amount * rate)

    def _recompute_aggregates(self) -> None:
        # Recompute EUR aggregates per symbol after conversions
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
