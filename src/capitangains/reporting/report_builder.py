from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .extract import DividendRow, InterestRow, SyepInterestRow, WithholdingRow
from .fifo import RealizedLine
from .fx import FxTable

logger = logging.getLogger(__name__)


@dataclass
class ReportBuilder:
    year: int

    def __post_init__(self) -> None:
        # collections
        self.realized_lines: list[RealizedLine] = []
        self.symbol_totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        self.dividends: list[DividendRow] = []
        self.withholding: list[WithholdingRow] = []
        self.syep_interest: list[SyepInterestRow] = []
        self.interest: list[InterestRow] = []
        self.transfers: list[Any] = []  # TransferRow objects

        # flags
        self.fx_needed: bool = False
        self.fx_missing: bool = False

    def add_realized(self, rl: RealizedLine) -> None:
        self.realized_lines.append(rl)
        # aggregate per symbol
        t = self.symbol_totals[rl.symbol]
        t["realized_ccy:" + rl.currency] += rl.realized_pl_ccy
        t["proceeds_ccy:" + rl.currency] += rl.sell_net_ccy
        t["alloc_cost_ccy:" + rl.currency] += sum(
            (leg.alloc_cost_ccy for leg in rl.legs), Decimal("0")
        )
        # EUR aggregations if present
        if rl.realized_pl_eur is not None:
            t["realized_eur"] += rl.realized_pl_eur
            t["proceeds_eur"] += rl.sell_net_eur or Decimal("0")
            t["alloc_cost_eur"] += rl.alloc_cost_eur or Decimal("0")

    def set_dividends(self, rows: list[DividendRow]) -> None:
        self.dividends = rows

    def set_withholding(self, rows: list[WithholdingRow]) -> None:
        self.withholding = rows

    def set_syep_interest(self, rows: list[SyepInterestRow]) -> None:
        self.syep_interest = rows

    def set_interest(self, rows: list[InterestRow]) -> None:
        self.interest = rows

    def set_transfers(self, transfers: list[Any]) -> None:
        self.transfers = transfers

    def convert_eur(self, fx: FxTable | None) -> None:
        """Convert realized lines to EUR using per-date FX if available.

        PT practice: acquisition values -> EUR at buy date; sale values -> EUR at sale
        date.
        """
        if fx is None:
            # Mark if any non-EUR currency is present.  Note that we will still fill
            # EUR-native trades.
            self.fx_missing = any(rl.currency != "EUR" for rl in self.realized_lines)

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
        # allocate sale net EUR across legs by quantity share (helps Annex J)
        if rl.sell_qty != 0:
            for leg in rl.legs:
                share = leg.qty / rl.sell_qty
                leg.proceeds_share_eur = (rl.sell_net_eur * share).quantize(
                    Decimal("0.01")
                )

    def _convert_realized_line_fx(self, rl: RealizedLine, fx: FxTable) -> None:
        sell_rate = fx.get_rate(rl.sell_date, rl.currency)
        if sell_rate is None:
            self.fx_missing = True
            return

        rl.sell_gross_eur = (rl.sell_gross_ccy * sell_rate).quantize(Decimal("0.01"))
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
        # allocate sale net EUR across legs by quantity share
        if rl.sell_qty != 0 and rl.sell_net_eur is not None:
            for leg in rl.legs:
                share = leg.qty / rl.sell_qty
                leg.proceeds_share_eur = (rl.sell_net_eur * share).quantize(
                    Decimal("0.01")
                )

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
            if "realized_eur" in totals:
                totals["realized_eur"] = Decimal("0")
            if "proceeds_eur" in totals:
                totals["proceeds_eur"] = Decimal("0")
            if "alloc_eur" in totals:
                totals["alloc_eur"] = Decimal("0")

        for rl in self.realized_lines:
            t = self.symbol_totals[rl.symbol]
            if rl.realized_pl_eur is not None:
                t["realized_eur"] += rl.realized_pl_eur
            if rl.sell_net_eur is not None:
                t["proceeds_eur"] += rl.sell_net_eur
            if rl.alloc_cost_eur is not None:
                t["alloc_eur"] += rl.alloc_cost_eur
