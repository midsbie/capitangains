from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

from capitangains.logging import configure_logging
from .fifo import RealizedLine
from .fx import FxTable

logger = configure_logging()


@dataclass
class ReportBuilder:
    year: int

    def __post_init__(self):
        # collections
        self.realized_lines: list[RealizedLine] = []
        self.symbol_totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        self.dividends: list[dict[str, Any]] = []
        self.withholding: list[dict[str, Any]] = []
        self.syep_interest: list[dict[str, Any]] = []
        self.interest: list[dict[str, Any]] = []

        # flags
        self.fx_needed: bool = False
        self.fx_missing: bool = False

    def add_realized(self, rl: RealizedLine):
        self.realized_lines.append(rl)
        # aggregate per symbol
        t = self.symbol_totals[rl.symbol]
        t["realized_ccy:" + rl.currency] += rl.realized_pl_ccy
        t["proceeds_ccy:" + rl.currency] += rl.sell_net_ccy
        t["alloc_cost_ccy:" + rl.currency] += sum(
            (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
        )
        # EUR aggregations if present
        if rl.realized_pl_eur is not None:
            t["realized_eur"] += rl.realized_pl_eur
            t["proceeds_eur"] += rl.sell_net_eur or Decimal("0")
            t["alloc_cost_eur"] += rl.alloc_cost_eur or Decimal("0")

    def set_dividends(self, rows: list[dict[str, Any]]):
        self.dividends = rows

    def set_withholding(self, rows: list[dict[str, Any]]):
        self.withholding = rows

    def set_syep_interest(self, rows: list[dict[str, Any]]):
        self.syep_interest = rows

    def set_interest(self, rows: list[dict[str, Any]]):
        self.interest = rows

    # CSV/Markdown writers have been intentionally removed in favor of ReportSink-based outputs.

    def convert_eur(self, fx: Optional[FxTable]):
        """Convert realized lines to EUR using per-date FX if available.
        PT practice: acquisition values -> EUR at buy date; sale values -> EUR at sale date.
        """
        if fx is None:
            # Mark if any non-EUR currency is present. We will still fill EUR-native trades.
            self.fx_missing = any(rl.currency != "EUR" for rl in self.realized_lines)

        for rl in self.realized_lines:
            if rl.currency == "EUR":
                rl.sell_gross_eur = rl.sell_gross_ccy
                rl.sell_comm_eur = rl.sell_comm_ccy
                rl.sell_net_eur = rl.sell_net_ccy
                alloc_eur = Decimal("0")
                # per-leg EUR breakdown (identity conversion)
                for leg in rl.legs:
                    leg["alloc_cost_eur"] = leg["alloc_cost_ccy"]
                    alloc_eur += leg["alloc_cost_eur"]
                rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
                rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
                    Decimal("0.01")
                )
                # allocate sale net EUR across legs by quantity share (helps Annex G)
                if rl.sell_qty != 0:
                    for leg in rl.legs:
                        share = leg["qty"] / rl.sell_qty
                        leg["proceeds_share_eur"] = (rl.sell_net_eur * share).quantize(
                            Decimal("0.01")
                        )
                continue

            # Non-EUR needs FX
            if fx is None:
                # Cannot convert without FX
                continue
            sell_rate = fx.get_rate(rl.sell_date, rl.currency)
            if sell_rate is None:
                self.fx_missing = True
                continue
            rl.sell_gross_eur = (rl.sell_gross_ccy * sell_rate).quantize(
                Decimal("0.01")
            )
            rl.sell_comm_eur = (rl.sell_comm_ccy * sell_rate).quantize(Decimal("0.01"))
            rl.sell_net_eur = (rl.sell_net_ccy * sell_rate).quantize(Decimal("0.01"))

            alloc_eur = Decimal("0")
            for leg in rl.legs:
                bd = leg["buy_date"]
                if bd is None:
                    rate = sell_rate  # fallback
                else:
                    rate = fx.get_rate(bd, rl.currency) or sell_rate
                leg_eur = (leg["alloc_cost_ccy"] * rate).quantize(Decimal("0.01"))
                leg["alloc_cost_eur"] = leg_eur
                alloc_eur += leg_eur
            rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
            rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
                Decimal("0.01")
            )
            # allocate sale net EUR across legs by quantity share
            if rl.sell_qty != 0 and rl.sell_net_eur is not None:
                for leg in rl.legs:
                    share = leg["qty"] / rl.sell_qty
                    leg["proceeds_share_eur"] = (rl.sell_net_eur * share).quantize(
                        Decimal("0.01")
                    )

        # Convert SYEP interest to EUR, if available
        if getattr(self, "syep_interest", None):
            for row in self.syep_interest:
                cur = (row.get("currency") or "").upper()
                amt = row.get("interest_paid")
                d = row.get("value_date")
                if amt is None:
                    continue
                if cur == "EUR":
                    row["interest_paid_eur"] = amt.quantize(Decimal("0.01"))
                    continue
                if fx is None or d is None:
                    continue
                rate = fx.get_rate(d, cur)
                if rate is None:
                    self.fx_missing = True
                    continue
                row["interest_paid_eur"] = (amt * rate).quantize(Decimal("0.01"))

        # Convert Withholding Tax amounts to EUR, if possible
        if getattr(self, "withholding", None):
            for row in self.withholding:
                cur = (row.get("currency") or "").upper()
                amt = row.get("amount")
                d = row.get("date")
                if amt is None:
                    continue
                if cur == "EUR":
                    row["amount_eur"] = amt.quantize(Decimal("0.01"))
                    continue
                if fx is None or d is None:
                    continue
                rate = fx.get_rate(d, cur)
                if rate is None:
                    self.fx_missing = True
                    continue
                row["amount_eur"] = (amt * rate).quantize(Decimal("0.01"))

        # Convert Dividends amounts to EUR, if possible
        if getattr(self, "dividends", None):
            for row in self.dividends:
                cur = (row.get("currency") or "").upper()
                amt = row.get("amount")
                d = row.get("date")
                if amt is None:
                    continue
                if cur == "EUR":
                    row["amount_eur"] = amt.quantize(Decimal("0.01"))
                    continue
                if fx is None or d is None:
                    continue
                rate = fx.get_rate(d, cur)
                if rate is None:
                    self.fx_missing = True
                    continue
                row["amount_eur"] = (amt * rate).quantize(Decimal("0.01"))

        # Convert Interest amounts to EUR, if possible
        if getattr(self, "interest", None):
            for row in self.interest:
                cur = (row.get("currency") or "").upper()
                amt = row.get("amount")
                d = row.get("date")
                if amt is None:
                    continue
                if cur == "EUR":
                    row["amount_eur"] = amt.quantize(Decimal("0.01"))
                    continue
                if fx is None or d is None:
                    continue
                rate = fx.get_rate(d, cur)
                if rate is None:
                    self.fx_missing = True
                    continue
                row["amount_eur"] = (amt * rate).quantize(Decimal("0.01"))
