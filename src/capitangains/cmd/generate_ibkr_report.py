"""
Analyze Interactive Brokers (IBKR) Activity Statement CSVs and produce a capital gains
report tailored for Portugal (FIFO, commissions included, EUR outputs when possible).

Highlights
----------
- Parses the **sectioned** IBKR Activity Statement CSV (not Flex Query).
- Handles multiple "Trades" header variants (IBKR mixes columns).
- Computes realized gains using **FIFO** with commissions/fees included:
  * Buy: commissions increase basis.
  * Sell: commissions reduce proceeds.
- Optional FX conversion via user-provided table (ECB/BdP daily rates).
- Produces human-readable Markdown and machine-readable CSV outputs:
  * realized_trades.csv (per sell, with matched buy-lots and EUR if available)
  * per_symbol_summary.csv
  * dividends.csv
  * withholding_tax.csv
  * report.md (Portuguese-filing-aligned summary, Annex G helper columns)
- Cross-check (soft) against IBKR "Realized & Unrealized Performance Summary"
  when numbers are parseable.

Usage
-----
    python generate_ibkr_report.py \
        --year 2024 \
        --input /path/to/ActivityStatement_2024.csv \
        --output-dir ./out \
        --fx-table ./fx_rates.csv

Arguments
---------
--year               Calendar year to report (YYYY).
--input              One or more IBKR Activity Statement CSV files.
--output-dir         Output directory (created if missing).
--asset-scope        Asset filter: 'stocks' (default), 'etfs', 'stocks_etfs', 'all'.
--fx-table           Optional CSV with daily FX rates (for non-EUR trades).
                     Expected columns: date,currency,eur_per_unit
                     Example row: 2024-10-30,USD,0.9421  (meaning 1 USD = 0.9421 EUR)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, getcontext
from pathlib import Path
from typing import Any, Optional, Union
from collections import defaultdict

from capitangains.core import date_key, parse_date, to_dec
from capitangains.logging import configure_logging
from capitangains.model import IbkrModel, IbkrStatementCsvParser

getcontext().prec = 28  # plenty of precision for money
getcontext().rounding = ROUND_HALF_UP

logger = configure_logging()

ASSET_STOCK_LIKE = {"Stocks", "Stock", "ETFs", "ETF", "ETCs", "ETP"}


@dataclass
class TradeRow:
    section: str
    asset_category: str
    currency: str
    symbol: str
    datetime_str: str
    date: dt.date
    quantity: Decimal  # positive buy, negative sell
    t_price: Decimal
    proceeds: Decimal  # signed: negative buy cash, positive sell cash
    comm_fee: Decimal  # signed: negative fee/commission, rarely positive rebates
    code: str


def parse_trades_stocklike(  # noqa: C901
    model: IbkrModel, asset_scope: str = "stocks"
) -> list[TradeRow]:
    """Extract stock-like trades from 'Trades' section across header variants.
    asset_scope: 'stocks', 'etfs', 'stocks_etfs', 'all'
    """
    scope_set = {
        "stocks": {"Stocks", "Stock"},
        "etfs": {"ETF", "ETFs", "ETCs", "ETP"},
        "stocks_etfs": {"Stocks", "Stock", "ETF", "ETFs", "ETCs", "ETP"},
        "all": None,
    }[asset_scope]

    trades: list[TradeRow] = []

    for sub in model.get_subtables("Trades"):
        header = [h.strip() for h in sub.header]
        rows = sub.rows

        # Try to locate relevant columns (be lenient)
        col = {
            k: None
            for k in [
                "DataDiscriminator",
                "Asset Category",
                "Currency",
                "Symbol",
                "Date/Time",
                "Quantity",
                "T. Price",
                "Proceeds",
                "Comm/Fee",
                "Code",
                "C. Price",
                "Comm in EUR",
                "MTM P/L",
                "MTM in EUR",
                "Basis",
            ]
        }
        for name in col:
            for i, h in enumerate(header):
                if h == name:
                    col[name] = i
                    break

        # Skip subtables without essential columns
        need_cols = [
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Quantity",
            "Proceeds",
            "Code",
        ]
        if any(col[n] is None for n in need_cols):
            continue

        for r in rows:
            asset_category = r.get("Asset Category", "").strip()
            if scope_set is not None and asset_category not in scope_set:
                continue
            currency = r.get("Currency", "").strip()
            symbol = r.get("Symbol", "").strip()
            dt_str = r.get("Date/Time", "").strip()
            qty_s = r.get("Quantity", "").strip()
            proceeds_s = r.get("Proceeds", "").strip()
            code = r.get("Code", "").strip()

            # T. Price may be missing in some rows, default 0
            t_price_s = r.get("T. Price", "").strip()

            # Commission column can be 'Comm/Fee' in stock trades; 'Comm in EUR' appears in some Forex tables.
            comm_s = ""
            if "Comm/Fee" in r:
                comm_s = r.get("Comm/Fee", "").strip()
            elif "Comm in EUR" in r:
                # Some subtables only have Comm in EUR (e.g., Forex); we don't use them here, but keep consistent type.
                comm_s = r.get("Comm in EUR", "").strip()
            else:
                comm_s = ""

            try:
                trade = TradeRow(
                    section="Trades",
                    asset_category=asset_category,
                    currency=currency,
                    symbol=symbol,
                    datetime_str=dt_str,
                    date=parse_date(dt_str),
                    quantity=to_dec(qty_s),
                    t_price=to_dec(t_price_s),
                    proceeds=to_dec(proceeds_s),
                    comm_fee=to_dec(comm_s),
                    code=code,
                )
            except Exception:
                logger.exception("Failed to parse trade row %s", r)
                continue

            # Only track non-zero quantity
            if trade.quantity == 0:
                continue

            trades.append(trade)

    # Sort by actual execution date/time for deterministic FIFO (buys before sells if
    # same timestamp? use quantity sign)
    trades.sort(key=lambda tr: (tr.date, tr.datetime_str, tr.quantity <= 0))
    return trades


def parse_dividends(model: IbkrModel) -> list[dict[str, Any]]:
    out = []
    for r in model.iter_rows("Dividends"):
        # Header: Currency,Date,Description,Amount
        cur = r.get("Currency", "").strip()
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amt = to_dec(r.get("Amount", ""))
        if cur and date_s and desc:
            out.append(
                {
                    "currency": cur,
                    "date": parse_date(date_s),
                    "description": desc,
                    "amount": amt,
                }
            )
    return out


def parse_withholding_tax(model: IbkrModel) -> list[dict[str, Any]]:
    out = []
    for r in model.iter_rows("Withholding Tax"):
        cur = r.get("Currency", "").strip()
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amt = to_dec(r.get("Amount", ""))
        code = r.get("Code", "").strip() if "Code" in r else ""
        if cur and date_s and desc:
            out.append(
                {
                    "currency": cur,
                    "date": parse_date(date_s),
                    "description": desc,
                    "amount": amt,
                    "code": code,
                }
            )
    return out


class FxTable:
    """Simple date-indexed FX table: (date, currency) -> eur_per_unit.

    CSV format required:
        date,currency,eur_per_unit
        2024-01-02,USD,0.9123
        2024-01-02,GBP,1.1620
    """

    def __init__(self):
        # Map: currency -> { date -> Decimal(eur_per_unit) }, plus sorted date list
        self.data: dict[str, dict[str, Decimal]] = defaultdict(dict)
        self.date_index: dict[str, list[str]] = {}

    @classmethod
    def from_csv(cls, path: Union[str, Path]) -> "FxTable":
        inst = cls()
        with open(path, "r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            required = {"date", "currency", "eur_per_unit"}
            missing = required - set((reader.fieldnames or []))
            if missing:
                raise ValueError(f"FX table missing columns: {missing}")
            for row in reader:
                d = date_key(row["date"])
                ccy = row["currency"].strip().upper()
                rate = to_dec(row["eur_per_unit"])
                inst.data[ccy][d] = rate
        for ccy, m in inst.data.items():
            inst.date_index[ccy] = sorted(m.keys())
        return inst

    def get_rate(self, date: dt.date, currency: str) -> Optional[Decimal]:
        c = currency.upper()
        if c == "EUR":
            return Decimal("1")
        if c not in self.data:
            return None
        d = date.isoformat()
        if d in self.data[c]:
            return self.data[c][d]
        # fallback to nearest previous date (weekends/holidays)
        # Find the latest date <= d in sorted list
        dates = self.date_index[c]
        # binary search
        import bisect

        pos = bisect.bisect_right(dates, d)
        if pos == 0:
            return None
        return self.data[c][dates[pos - 1]]


@dataclass
class Lot:
    buy_date: dt.date
    qty: Decimal  # remaining quantity in lot
    basis_ccy: Decimal  # total basis in trade currency (incl. buy fees)
    currency: str


@dataclass
class RealizedLine:
    symbol: str
    currency: str
    sell_date: dt.date
    # positive quantity sold (abs of trade negative qty)
    sell_qty: Decimal
    sell_gross_ccy: Decimal  # abs(proceeds) before fees
    sell_comm_ccy: Decimal  # signed (typically negative)
    sell_net_ccy: Decimal  # gross + comm (fees reduce proceeds)
    # Lot matches (for audit trail / Annex G helper)
    # each: {buy_date, qty, alloc_cost_ccy, buy_comm_ccy?, ...}
    legs: list[dict[str, Any]]
    realized_pl_ccy: Decimal

    # EUR conversions (optional, if FX available)
    sell_gross_eur: Optional[Decimal] = None
    sell_comm_eur: Optional[Decimal] = None
    sell_net_eur: Optional[Decimal] = None
    alloc_cost_eur: Optional[Decimal] = None
    realized_pl_eur: Optional[Decimal] = None


class FifoMatcher:
    def __init__(self):
        # positions[symbol] = deque[Lot]
        self.positions: dict[str, deque] = defaultdict(deque)

    @staticmethod
    def _buy_cost_ccy(tr: TradeRow) -> Decimal:
        # Buy cash outflow = -proceeds - comm_fee  (both signed)
        # Example: proceeds = -1000, comm = -1  => 1001
        return (-tr.proceeds) - tr.comm_fee

    @staticmethod
    def _sell_gross_ccy(tr: TradeRow) -> Decimal:
        # Sell gross cash inflow (before fees): proceeds is positive
        return tr.proceeds.copy_abs()

    @staticmethod
    def _sell_net_ccy(tr: TradeRow) -> Decimal:
        # Net proceeds after fees: proceeds + comm_fee (comm_fee negative reduces)
        return tr.proceeds + tr.comm_fee

    def ingest(self, trade: TradeRow) -> Optional[RealizedLine]:
        if trade.quantity > 0:
            # BUY
            lot = Lot(
                buy_date=trade.date,
                qty=trade.quantity,
                basis_ccy=self._buy_cost_ccy(trade),
                currency=trade.currency,
            )
            self.positions[trade.symbol].append(lot)
            return None
        else:
            # SELL
            qty_to_sell = -trade.quantity  # positive
            legs: list[dict[str, Any]] = []
            alloc_cost_ccy = Decimal("0")

            while qty_to_sell > 0 and self.positions[trade.symbol]:
                lot = self.positions[trade.symbol][0]
                take = min(qty_to_sell, lot.qty)
                # proportional cost from this lot
                ratio = (take / lot.qty) if lot.qty != 0 else Decimal("0")
                cost_piece = (lot.basis_ccy * ratio).quantize(Decimal("0.00000001"))
                alloc_cost_ccy += cost_piece
                legs.append(
                    {
                        "buy_date": lot.buy_date,
                        "qty": take,
                        "lot_qty_before": lot.qty,
                        "alloc_cost_ccy": cost_piece,
                    }
                )
                # reduce lot
                lot.qty -= take
                lot.basis_ccy -= cost_piece
                qty_to_sell -= take
                if lot.qty == 0:
                    self.positions[trade.symbol].popleft()

            if qty_to_sell > 0:
                # short sell or not enough lots
                logger.warning(
                    "Not enough lots for %s on %s; remaining qty=%s. Treating remainder as zero-cost.",
                    trade.symbol,
                    trade.date,
                    qty_to_sell,
                )
                # Treat remainder as zero-cost to avoid crash
                legs.append(
                    {
                        "buy_date": None,
                        "qty": qty_to_sell,
                        "lot_qty_before": Decimal("0"),
                        "alloc_cost_ccy": Decimal("0"),
                    }
                )
                alloc_cost_ccy += Decimal("0")
                qty_to_sell = Decimal("0")

            sell_gross = self._sell_gross_ccy(trade)
            sell_net = self._sell_net_ccy(trade)
            realized_ccy = (sell_net - alloc_cost_ccy).quantize(Decimal("0.01"))

            return RealizedLine(
                symbol=trade.symbol,
                currency=trade.currency,
                sell_date=trade.date,
                sell_qty=(-trade.quantity),
                sell_gross_ccy=sell_gross,
                sell_comm_ccy=trade.comm_fee,
                sell_net_ccy=sell_net,
                legs=legs,
                realized_pl_ccy=realized_ccy,
            )


class ReportBuilder:
    def __init__(self, year: int, out_dir: Union[str, Path]):
        self.year = year
        self.out_dir = Path(out_dir)
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
            for s, d in self.symbol_totals.items():
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
            (rl.realized_pl_eur or Decimal("0") for rl in self.realized_lines),
            Decimal("0"),
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
                    or totals.get("realized_eur", Decimal("0")) == 0
                ):
                    continue
                fp.write(
                    f"| {sym} | {totals.get('realized_eur', Decimal('0'))} | "
                    f"{totals.get('proceeds_eur', Decimal('0'))} | "
                    f"{totals.get('alloc_cost_eur', Decimal('0'))} |\n"
                )

            if self.dividends:
                fp.write("\n## Dividends (not part of capital gains)\n\n")
                fp.write("| Date | CCY | Description | Amount |\n|---|---|---|---:|\n")
                for d in self.dividends:
                    fp.write(
                        f"| {d['date'].isoformat()} | {d['currency']} | {
                            d['description']
                        } | {d['amount']} |\n"
                    )

            if self.withholding:
                fp.write("\n## Withholding Tax (for Annex E/J reference)\n\n")
                fp.write(
                    "| Date | CCY | Description | Amount | Code |\n|---|---|---|---:|---|\n"
                )
                for d in self.withholding:
                    fp.write(
                        f"| {d['date'].isoformat()} | {d['currency']} | {
                            d['description']
                        } | {d['amount']} | {d.get('code', '')} |\n"
                    )

            fp.write("\n## Annex G Helper (per sell, FIFO-matched)\n\n")
            fp.write(
                "| Symbol | Sell Date | Qty | Currency | Sell Net (CCY) | Alloc Cost (CCY) | Result (CCY) | Sell Net (EUR) | Alloc Cost (EUR) | Result (EUR) |\n"
            )
            fp.write("|---|---|---:|---|---:|---:|---:|---:|---:|---:|\n")
            for rl in self.realized_lines:
                alloc_cost_ccy = sum(
                    (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
                )
                fp.write(
                    f"| {rl.symbol} | {rl.sell_date.isoformat()} | {rl.sell_qty} | {rl.currency} | "
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
                bd: Optional[dt.date] = leg["buy_date"]
                if bd is None:
                    rate = sell_rate  # fallback
                else:
                    rate = fx.get_rate(bd, rl.currency) or sell_rate
                alloc_eur += leg["alloc_cost_ccy"] * rate
            rl.alloc_cost_eur = alloc_eur.quantize(Decimal("0.01"))
            rl.realized_pl_eur = (rl.sell_net_eur - rl.alloc_cost_eur).quantize(
                Decimal("0.01")
            )


def reconcile_with_ibkr_summary(model: IbkrModel) -> dict[str, Decimal]:
    """Try to read 'Realized & Unrealized Performance Summary' for Stocks per-symbol realized EUR.
    Returns map: symbol -> realized_eur. If parsing fails (sanitized CSV), returns empty dict.
    """
    result: dict[str, Decimal] = {}
    for sub in model.get_subtables("Realized & Unrealized Performance Summary"):
        header = [h.strip() for h in sub.header]
        rows = sub.rows
        # Heuristic: Find columns for Asset Category, Symbol, Total (or Realized Total) etc.
        # In many IBKR statements, columns include fields for realized/unrealized P/L and a final "Total".
        try:
            idx_asset = header.index("Asset Category")
        except ValueError:
            continue

        # Try to find symbol column: sometimes it's at index 2 (after "Asset Category")
        idx_symbol = None
        for name in ["Symbol", "Ticker", "Description"]:
            if name in header:
                idx_symbol = header.index(name)
                break
        if idx_symbol is None:
            # fall back: assume second column is the symbol bucket
            idx_symbol = 2 if len(header) > 2 else None

        # Try to find a realized EUR column. Heuristic: pick the last numeric-looking column.
        # Because in some sanitized exports values are elided with "...", we may fail.
        numeric_cols = [
            i
            for i, h in enumerate(header)
            if re.search(r"(Total|Realized|P/L|Profit|Loss)", h, re.I)
        ]
        candidate_cols = numeric_cols or list(
            range(len(header) - 1, max(-1, len(header) - 10), -1)
        )

        for r in rows:
            asset = r.get("Asset Category", "")
            if asset not in ASSET_STOCK_LIKE:
                continue
            sym = (
                r.get(header[idx_symbol], "").strip() if idx_symbol is not None else ""
            )
            if not sym:
                continue
            # try columns from right to left for a parseable number
            val = None
            for ci in reversed(candidate_cols):
                v = r.get(header[ci], "")
                dec = to_dec(v)
                # If parsing was hopeless (due to "..." elision), result might be zero; skip zeros
                if dec != 0:
                    val = dec
                    break
            if val is not None:
                result[sym] = result.get(sym, Decimal("0")) + val

    return result


def process_files(args):
    # Parse CSVs
    logger.info("Reading %s", args.input)

    parser = IbkrStatementCsvParser()
    model, report = parser.parse_file(args.input)
    # Still log the report (non-fatal diagnostics) for parity with prior behavior.
    report.log_with(logger)

    # Extract data
    trades = parse_trades_stocklike(model, asset_scope=args.asset_scope)
    dividends = parse_dividends(model)
    withholding = parse_withholding_tax(model)

    # Build FIFO realized
    matcher = FifoMatcher()
    realized: list[RealizedLine] = []
    for tr in trades:
        rl = matcher.ingest(tr)
        if rl is not None and rl.sell_date.year == args.year:
            realized.append(rl)

    # Build report
    rb = ReportBuilder(year=args.year, out_dir=args.output_dir)
    for rl in realized:
        rb.add_realized(rl)
    rb.set_dividends([d for d in dividends if d["date"].year == args.year])
    rb.set_withholding([w for w in withholding if w["date"].year == args.year])

    # FX conversion if provided
    fx = None
    if args.fx_table:
        try:
            fx = FxTable.from_csv(args.fx_table)
        except Exception as e:
            logger.exception("Failed to read FX table: %s", e)
            fx = None
    rb.convert_eur(fx)

    # Soft reconciliation
    try:
        ibkr_sum = reconcile_with_ibkr_summary(model)
        if ibkr_sum:
            # Compare per-symbol realized EUR if we have EUR conversions
            mismatches = []
            for sym, ibkr_val in ibkr_sum.items():
                my_val = rb.symbol_totals.get(sym, {}).get("realized_eur", None)
                if my_val is not None:
                    if (my_val - ibkr_val).copy_abs() > Decimal("0.05"):
                        mismatches.append((sym, my_val, ibkr_val))
            if mismatches:
                logger.warning(
                    "Reconciliation mismatches (my EUR vs IBKR EUR): %s", mismatches[:5]
                )
    except Exception:
        logger.exception("Reconciliation failed; continuing without it.")

    # Write outputs
    rb.write_csvs()
    rb.write_report()

    logger.info("Wrote outputs to %s", args.output_dir)


def build_argparser():
    p = argparse.ArgumentParser(
        description="Portugal Capital Gains Report from IBKR Activity Statement CSV"
    )
    p.add_argument(
        "--year", type=int, required=True, help="Calendar year to report (YYYY)"
    )
    p.add_argument(
        "--input",
        type=str,
        required=True,
        help="One or more Activity Statement CSV paths",
    )
    p.add_argument(
        "--output-dir", type=str, default="out_report", help="Output directory"
    )
    p.add_argument(
        "--asset-scope",
        type=str,
        default="stocks",
        choices=["stocks", "etfs", "stocks_etfs", "all"],
        help="Asset filter scope",
    )
    p.add_argument(
        "--fx-table",
        type=str,
        default=None,
        help="CSV of daily FX rates (date,currency,eur_per_unit)",
    )
    return p


def main():
    parser = build_argparser()
    args = parser.parse_args()
    process_files(args)


if __name__ == "__main__":
    main()
