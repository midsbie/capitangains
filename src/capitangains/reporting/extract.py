from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from capitangains.conv import parse_date, to_dec
from capitangains.logging import configure_logging
from capitangains.model import IbkrModel

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


def parse_trades_stocklike(
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
            logger.debug("Skipping Trades subtable, missing cols: %s", col)
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

            # Commission column can be 'Comm/Fee' in stock trades; 'Comm in EUR' appears
            # in some Forex tables.
            comm_s = ""
            if "Comm/Fee" in r:
                comm_s = r.get("Comm/Fee", "").strip()
            elif "Comm in EUR" in r:
                # Some subtables only have Comm in EUR (e.g., Forex); we don't use them
                # here, but keep consistent type.
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
