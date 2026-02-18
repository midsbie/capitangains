from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .extract import DividendRow, InterestRow, SyepInterestRow, WithholdingRow
from .fifo import RealizedLine
from .fifo_domain import SellMatchLeg, TransferProtocol
from .fx import FxTable

logger = logging.getLogger(__name__)


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
    # Flags
    fx_needed: bool = False
    fx_missing: bool = False

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
            elif fx is not None:
                self._convert_realized_line_fx(rl, fx)
            else:
                self.fx_missing = True

    def _convert_realized_line_eur(self, rl: RealizedLine) -> None:
        rl.sell_gross_eur = rl.sell_gross_ccy
        rl.sell_comm_eur = rl.sell_comm_ccy
        rl.sell_net_eur = rl.sell_net_ccy
        alloc_eur = Decimal("0")
        # per-leg EUR breakdown (identity conversion)
        for leg in rl.legs:
            leg.alloc_cost_eur = leg.alloc_cost_ccy
            alloc_eur += leg.alloc_cost_eur
        rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
        rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
            Decimal("0.01")
        )
        self._allocate_proceeds_to_legs(rl.legs, rl.sell_qty, rl.sell_net_eur)

    def _convert_realized_line_fx(self, rl: RealizedLine, fx: FxTable) -> None:
        sell_rate = fx.get_rate(rl.sell_date, rl.currency)
        if sell_rate is None:
            logger.debug(
                "Sell FX rate missing for %s on %s, proceeds marked as missing",
                rl.currency,
                rl.sell_date,
            )
            self.fx_missing = True
            return

        proceeds_eur = (rl.sell_gross_ccy * sell_rate).quantize(Decimal("0.01"))
        logger.debug(
            "Sell FX conversion: %s %s: EUR (rate: %s) = %s EUR",
            rl.sell_gross_ccy,
            rl.currency,
            sell_rate,
            proceeds_eur,
        )

        rl.sell_gross_eur = proceeds_eur
        rl.sell_comm_eur = (rl.sell_comm_ccy * sell_rate).quantize(Decimal("0.01"))
        rl.sell_net_eur = (rl.sell_net_ccy * sell_rate).quantize(Decimal("0.01"))

        alloc_eur = Decimal("0")
        for leg in rl.legs:
            bd = leg.buy_date
            rate = sell_rate  # fallback
            if bd is not None:
                rate = fx.get_rate(bd, rl.currency) or sell_rate
            leg_eur = (leg.alloc_cost_ccy * rate).quantize(Decimal("0.01"))
            leg.alloc_cost_eur = leg_eur
            alloc_eur += leg_eur
        rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
        rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
            Decimal("0.01")
        )
        self._allocate_proceeds_to_legs(rl.legs, rl.sell_qty, rl.sell_net_eur)

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
        cent = Decimal("0.01")
        allocated = Decimal("0")
        for leg in legs[:-1]:
            leg.proceeds_share_eur = (sell_net_eur * leg.qty / sell_qty).quantize(cent)
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
        """Convert a single amount to EUR using FX rates.

        Returns the EUR amount, or None if conversion is not possible (missing FX data).
        """
        cur = currency.upper()

        if cur == "EUR":
            return amount.quantize(Decimal("0.01"))

        if fx is None or date is None:
            self.fx_missing = True
            return None

        rate = fx.get_rate(date, cur)
        if rate is None:
            self.fx_missing = True
            return None

        return (amount * rate).quantize(Decimal("0.01"))

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
