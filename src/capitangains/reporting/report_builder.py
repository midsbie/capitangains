from __future__ import annotations

import csv
import json
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
    out_dir: Union[str, Path]

    def __post_init__(self):
        self.out_dir = Path(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # collections
        self.realized_lines: list[RealizedLine] = []
        self.symbol_totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        self.dividends: list[dict[str, Any]] = []
        self.withholding: list[dict[str, Any]] = []

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

    def write_csvs(self):
        # realized_trades.csv
        rt_path = self.out_dir / "realized_trades.csv"
        with open(rt_path, "w", encoding="utf-8", newline="") as fp:
            w = csv.writer(fp)
            w.writerow(
                [
                    "symbol",
                    "currency",
                    "sell_date",
                    "sell_qty",
                    "sell_gross_ccy",
                    "sell_comm_ccy",
                    "sell_net_ccy",
                    "alloc_cost_ccy",
                    "realized_pl_ccy",
                    "sell_gross_eur",
                    "sell_comm_eur",
                    "sell_net_eur",
                    "alloc_cost_eur",
                    "realized_pl_eur",
                    "legs_json",
                ]
            )
            for rl in self.realized_lines:
                alloc_cost_ccy = sum(
                    (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
                )
                legs_json = json.dumps(
                    [
                        {
                            "buy_date": (
                                ld["buy_date"].isoformat() if ld["buy_date"] else None
                            ),
                            "qty": str(ld["qty"]),
                            "alloc_cost_ccy": str(ld["alloc_cost_ccy"]),
                        }
                        for ld in rl.legs
                    ]
                )
                w.writerow(
                    [
                        rl.symbol,
                        rl.currency,
                        rl.sell_date.isoformat(),
                        str(rl.sell_qty),
                        str(rl.sell_gross_ccy),
                        str(rl.sell_comm_ccy),
                        str(rl.sell_net_ccy),
                        str(alloc_cost_ccy),
                        str(rl.realized_pl_ccy),
                        (
                            str(rl.sell_gross_eur)
                            if rl.sell_gross_eur is not None
                            else ""
                        ),
                        (str(rl.sell_comm_eur) if rl.sell_comm_eur is not None else ""),
                        (str(rl.sell_net_eur) if rl.sell_net_eur is not None else ""),
                        (
                            str(rl.alloc_cost_eur)
                            if rl.alloc_cost_eur is not None
                            else ""
                        ),
                        (
                            str(rl.realized_pl_eur)
                            if rl.realized_pl_eur is not None
                            else ""
                        ),
                        legs_json,
                    ]
                )

        # per_symbol_summary.csv
        ps_path = self.out_dir / "per_symbol_summary.csv"
        with open(ps_path, "w", encoding="utf-8", newline="") as fp:
            # Dynamically collect currencies seen
            all_ccy = set()
            for _, d in self.symbol_totals.items():
                for k in d.keys():
                    if k.startswith("realized_ccy:"):
                        all_ccy.add(k.split(":", 1)[1])
            fieldnames = (
                ["symbol"]
                + [f"realized_{ccy.lower()}" for ccy in sorted(all_ccy)]
                + ["realized_eur", "proceeds_eur", "alloc_cost_eur"]
            )
            w = csv.DictWriter(fp, fieldnames=fieldnames)
            w.writeheader()
            for symbol, totals in sorted(self.symbol_totals.items()):
                row = {"symbol": symbol}
                for ccy in sorted(all_ccy):
                    row[f"realized_{ccy.lower()}"] = str(
                        totals.get("realized_ccy:" + ccy, Decimal("0"))
                    )
                row["realized_eur"] = str(totals.get("realized_eur", Decimal("0")))
                row["proceeds_eur"] = str(totals.get("proceeds_eur", Decimal("0")))
                row["alloc_cost_eur"] = str(totals.get("alloc_cost_eur", Decimal("0")))
                w.writerow(row)

        # dividends.csv
        if self.dividends:
            dv_path = self.out_dir / "dividends.csv"
            with open(dv_path, "w", encoding="utf-8", newline="") as fp:
                w = csv.writer(fp)
                w.writerow(["date", "currency", "description", "amount"])
                for d in self.dividends:
                    w.writerow(
                        [
                            d["date"].isoformat(),
                            d["currency"],
                            d["description"],
                            str(d["amount"]),
                        ]
                    )

        # withholding_tax.csv
        if self.withholding:
            wt_path = self.out_dir / "withholding_tax.csv"
            with open(wt_path, "w", encoding="utf-8", newline="") as fp:
                w = csv.writer(fp)
                w.writerow(["date", "currency", "description", "amount", "code"])
                for d in self.withholding:
                    w.writerow(
                        [
                            d["date"].isoformat(),
                            d["currency"],
                            d["description"],
                            str(d["amount"]),
                            d.get("code", ""),
                        ]
                    )

    def write_report(self):
        rpt_path = self.out_dir / "report.md"
        total_eur = sum(
            (rl.realized_pl_eur or Decimal("0") for rl in self.realized_lines), Decimal("0")
        )
        total_ccy_by_cur: defaultdict[str, Decimal] = defaultdict(Decimal)
        for rl in self.realized_lines:
            total_ccy_by_cur[rl.currency] += rl.realized_pl_ccy

        with open(rpt_path, "w", encoding="utf-8") as fp:
            fp.write(f"# Capital Gains Report — Portugal — {self.year}\n\n")
            fp.write("**Method:** FIFO (commissions included in basis/proceeds). ")
            if self.fx_missing:
                fp.write(
                    "**Warning:** Some non-EUR trades missing FX; EUR totals are partial.\n\n"
                )
            else:
                fp.write("\n")
            fp.write("## Summary\n\n")
            if total_eur != 0:
                fp.write(f"- **Total realized (EUR):** {total_eur}\n")
            for cur, amt in sorted(total_ccy_by_cur.items()):
                fp.write(f"- **Total realized ({cur}):** {amt}\n")
            fp.write("\n")

            fp.write("## By Symbol (EUR)\n\n")
            fp.write("| Symbol | Realized EUR | Proceeds EUR | Alloc Cost EUR |\n")
            fp.write("|---|---:|---:|---:|\n")
            for sym, totals in sorted(self.symbol_totals.items()):
                if (
                    totals.get("realized_eur") is None
                    or totals.get("realized_eur", Decimal("0")) == Decimal("0")
                ):
                    continue
                fp.write(
                    f"| {sym} | {totals.get('realized_eur', Decimal('0'))} | "
                    f"{totals.get('proceeds_eur', Decimal('0'))} | {totals.get('alloc_cost_eur', Decimal('0'))} |\n"
                )

            fp.write("\n## Realized Trades (detail)\n\n")
            fp.write(
                "| Symbol | Date | Qty | Net Proceeds (CCY) | Alloc Cost (CCY) | P/L (CCY) | Net Proceeds (EUR) | Alloc Cost (EUR) | P/L (EUR) |\n"
            )
            fp.write("|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
            for rl in self.realized_lines:
                alloc_cost_ccy = sum(
                    (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
                )
                fp.write(
                    f"| {rl.symbol} | {rl.sell_date.isoformat()} | {rl.sell_qty} | "
                    f"{rl.sell_net_ccy} | {alloc_cost_ccy} | {rl.realized_pl_ccy} | "
                    f"{'' if rl.sell_net_eur is None else rl.sell_net_eur} | "
                    f"{'' if rl.alloc_cost_eur is None else rl.alloc_cost_eur} | "
                    f"{'' if rl.realized_pl_eur is None else rl.realized_pl_eur} |\n"
                )

    def convert_eur(self, fx: Optional[FxTable]):
        """Convert realized lines to EUR using per-date FX if available.
        PT practice: acquisition values -> EUR at buy date; sale values -> EUR at sale date.
        """
        if fx is None:
            # Mark if any non-EUR currency is present
            self.fx_missing = any(rl.currency != "EUR" for rl in self.realized_lines)
            return

        for rl in self.realized_lines:
            if rl.currency == "EUR":
                rl.sell_gross_eur = rl.sell_gross_ccy
                rl.sell_comm_eur = rl.sell_comm_ccy
                rl.sell_net_eur = rl.sell_net_ccy
                alloc_eur = sum(
                    (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
                )
                rl.alloc_cost_eur = alloc_eur
                rl.realized_pl_eur = rl.sell_net_eur - rl.alloc_cost_eur
                continue

            # Non-EUR needs FX
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
                alloc_eur += leg["alloc_cost_ccy"] * rate
            rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
            rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
                Decimal("0.01")
            )

