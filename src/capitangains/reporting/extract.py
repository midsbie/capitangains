from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import re

from capitangains.conv import parse_date, to_dec, to_dec_strict
from capitangains.model import IbkrModel


logger = logging.getLogger(__name__)

ALL_SCOPES_SET = {
    "stocks": {"Stocks", "Stock"},
    "etfs": {"ETF", "ETFs", "ETCs", "ETP"},
    "stocks_etfs": {"Stocks", "Stock", "ETF", "ETFs", "ETCs", "ETP"},
    "all": None,
}

ASSET_STOCK_LIKE = {"Stocks", "Stock", "ETFs", "ETF", "ETCs", "ETP"}

TRADE_COLS = [
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
    "Realized P/L",
]

NEED_TRADE_COLS = [
    "Asset Category",
    "Currency",
    "Symbol",
    "Date/Time",
    "Quantity",
    "Proceeds",
    "Code",
]


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
    basis_ccy: Decimal | None = None  # signed; sells are negative
    realized_pl_ccy: Decimal | None = None


@dataclass
class TransferRow:
    section: str
    asset_category: str
    currency: str
    symbol: str
    date: dt.date
    direction: str  # "In" or "Out"
    quantity: Decimal
    market_value: Decimal  # Cost basis for incoming transfers
    code: str


def parse_trades_stocklike_row(
    scope_set: set[str], r: dict[str, str], col: dict[str, int]
) -> TradeRow | None:
    asset_category = r.get("Asset Category", "").strip()
    if scope_set is not None and asset_category not in scope_set:
        return None

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

    # Optional Basis and Realized P/L if present
    basis_opt: Decimal | None = None
    if col.get("Basis") is not None:
        bs = r.get("Basis", "").strip()
        if bs != "":
            basis_opt = to_dec(bs)

    realized_opt: Decimal | None = None
    if col.get("Realized P/L") is not None:
        rs = r.get("Realized P/L", "").strip()
        if rs != "":
            realized_opt = to_dec(rs)

    trade = TradeRow(
        section="Trades",
        asset_category=asset_category,
        currency=currency,
        symbol=symbol,
        datetime_str=dt_str,
        date=parse_date(dt_str),
        quantity=to_dec_strict(qty_s),
        t_price=to_dec_strict(t_price_s),
        proceeds=to_dec_strict(proceeds_s),
        comm_fee=to_dec(comm_s),
        code=code,
        basis_ccy=basis_opt,
        realized_pl_ccy=realized_opt,
    )

    # Only track non-zero quantity
    return trade if trade.quantity != 0 else None


def parse_trades_stocklike(
    model: IbkrModel, asset_scope: str = "stocks"
) -> list[TradeRow]:
    """Extract stock-like trades from 'Trades' section across header variants.
    asset_scope: 'stocks', 'etfs', 'stocks_etfs', 'all'
    """
    scope_set = ALL_SCOPES_SET[asset_scope]
    trades: list[TradeRow] = []

    for sub in model.get_subtables("Trades"):
        header = [h.strip() for h in sub.header]
        rows = sub.rows

        # Try to locate relevant columns (be lenient)
        col = {k: None for k in TRADE_COLS}
        for name in col:
            for i, h in enumerate(header):
                if h == name:
                    col[name] = i
                    break

        # Skip subtables without essential columns
        if any(col[n] is None for n in NEED_TRADE_COLS):
            logger.debug("Skipping Trades subtable, missing cols: %s", col)
            continue

        for r in rows:
            try:
                trade = parse_trades_stocklike_row(scope_set, r, col)
                if trade is not None:
                    trades.append(trade)
            except Exception:
                logger.exception("Failed to parse trade row %s", r)

    # Sort by actual execution date/time for deterministic FIFO (buys before sells if
    # same timestamp use quantity sign)
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
            dlow = desc.lower()
            wtype = ""
            if (
                "credit interest" in dlow
                or "interest" in dlow
                and "dividend" not in dlow
            ):
                wtype = "Interest"
            elif "cash dividend" in dlow or "payment in lieu of dividend" in dlow:
                wtype = "Dividend"
            else:
                # default bucket: Dividend if it references dividend; otherwise leave empty
                wtype = "Dividend" if "dividend" in dlow else ""

            # Extract country from suffix like " - US Tax" or " - NL Tax"
            country = ""
            m = re.search(r"-\s+([A-Z]{2})\s+Tax\b", desc)
            if m:
                country = m.group(1)
            out.append(
                {
                    "currency": cur,
                    "date": parse_date(date_s),
                    "description": desc,
                    "amount": amt,
                    "code": code,
                    "type": wtype,
                    "country": country,
                }
            )
    return out


def parse_syep_interest_details(model: IbkrModel) -> list[dict[str, Any]]:
    """Parse 'Stock Yield Enhancement Program Securities Lent Interest Details'.

    Expected header:
      Currency, Value Date, Symbol, Start Date, Quantity, Collateral Amount,
      Market-based Rate (%), Interest Rate on Customer Collateral (%),
      Interest Paid to Customer, Code
    """
    out: list[dict[str, Any]] = []
    section = "Stock Yield Enhancement Program Securities Lent Interest Details"
    for r in model.iter_rows(section):
        cur = r.get("Currency", "").strip()
        value_date_s = r.get("Value Date", "").strip()
        sym = r.get("Symbol", "").strip()
        start_date_s = r.get("Start Date", "").strip()
        qty_s = r.get("Quantity", "").strip()
        collat_s = r.get("Collateral Amount", "").strip()
        mkt_rate_s = r.get("Market-based Rate (%)", "").strip()
        cust_rate_s = r.get("Interest Rate on Customer Collateral (%)", "").strip()
        paid_s = r.get("Interest Paid to Customer", "").strip()
        code = r.get("Code", "").strip()

        # Skip trailing totals like 'Total', 'Total in EUR'.
        if not cur or cur.lower().startswith("total"):
            continue

        row: dict[str, Any] = {
            "currency": cur,
            "value_date": (parse_date(value_date_s) if value_date_s else None),
            "symbol": sym,
            "start_date": (parse_date(start_date_s) if start_date_s else None),
            "quantity": to_dec(qty_s),
            "collateral_amount": to_dec(collat_s),
            "market_rate_pct": to_dec(mkt_rate_s),
            "customer_rate_pct": to_dec(cust_rate_s),
            "interest_paid": to_dec(paid_s),
            "code": code,
        }
        out.append(row)
    return out


def parse_interest(model: IbkrModel) -> list[dict[str, Any]]:
    """Parse 'Interest' section: credit/debit interest and monthly SYEP interest summaries.

    Header: Currency, Date, Description, Amount

    Excludes CSV total rows (e.g., 'Total', 'Total in EUR').
    """
    out: list[dict[str, Any]] = []
    for r in model.iter_rows("Interest"):
        cur = (r.get("Currency", "") or "").strip()
        if not cur or cur.lower().startswith("total"):
            continue
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
