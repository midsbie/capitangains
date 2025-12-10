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
            trade = parse_trades_stocklike_row(scope_set, r, col)
            if trade is not None:
                trades.append(trade)

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
        amount_s = r.get("Amount", "").strip()
        # Rows lacking currency/date/description are typically totals or non-data lines;
        # structural anomalies are already reported by the CSV parser, so we silently
        # filter these here rather than logging again.
        if cur and date_s and desc:
            amt = to_dec_strict(amount_s)
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
        amount_s = r.get("Amount", "").strip()
        code = r.get("Code", "").strip() if "Code" in r else ""
        # As with dividends, missing currency/date/description indicates totals or
        # non-data rows; malformed structure is handled at CSV parse time.
        if cur and date_s and desc:
            amt = to_dec_strict(amount_s)
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
                # default bucket: Dividend if it references dividend; otherwise leave
                # empty
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

        if not (qty_s and collat_s and mkt_rate_s and cust_rate_s and paid_s):
            raise ValueError(f"Invalid SYEP interest row (missing numeric fields): {r}")

        quantity = to_dec_strict(qty_s)
        collateral_amount = to_dec_strict(collat_s)
        market_rate_pct = to_dec_strict(mkt_rate_s)
        customer_rate_pct = to_dec_strict(cust_rate_s)
        interest_paid = to_dec_strict(paid_s)

        row: dict[str, Any] = {
            "currency": cur,
            "value_date": (parse_date(value_date_s) if value_date_s else None),
            "symbol": sym,
            "start_date": (parse_date(start_date_s) if start_date_s else None),
            "quantity": quantity,
            "collateral_amount": collateral_amount,
            "market_rate_pct": market_rate_pct,
            "customer_rate_pct": customer_rate_pct,
            "interest_paid": interest_paid,
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
        amount_s = r.get("Amount", "").strip()
        # Only rows with full currency/date/description are treated as interest lines:
        # - In IBKR exports, rows that fail this check are typically 'Total' or similar
        #   summary lines, which we intentionally ignore at the domain level.
        # - Truly malformed CSV structure is already surfaced by IbkrStatementCsvParser
        #   via ParseReport, so re-logging here would add noise without new signal.
        #
        # If we ever decide that a partial row here is an invariant violation, the
        # correct response would be to raise, not to emit a quiet debug log.
        if cur and date_s and desc:
            amt = to_dec_strict(amount_s)
            out.append(
                {
                    "currency": cur,
                    "date": parse_date(date_s),
                    "description": desc,
                    "amount": amt,
                }
            )
    return out


def parse_transfers(model: IbkrModel) -> list[TransferRow]:
    """Extract stock transfers from IBKR 'Transfers' section.

    Assumptions / invariants:
    - Only stock-like asset categories are considered (ASSET_STOCK_LIKE).
    - Direction must be 'In' or 'Out' (case-insensitive); other values are rejected.
    - Quantity must be strictly positive; zero/negative quantities are treated as errors.
    - For 'In' transfers, Market Value / Cost Basis must be present and parseable; missing
      or placeholder basis is treated as an error.
    - Transfers are applied as pre-period position seeding before processing trades.
    - Until Open Positions support is implemented, Market Value at transfer date is used
      as a proxy for cost basis, which may differ from IBKR's internal basis.
    """
    out: list[TransferRow] = []

    # "Transfers" section contains fields like:
    # Asset Category, Currency, Symbol, Date, Type, Direction, Qty, Cost Price, Cost Basis, Close Price, Market Value, Cash Amount, Code
    # The field names can vary. Key fields we need: Symbol, Date, Direction, Qty, Market Value (or Cost Basis?)

    # Based on user CSV:
    # Asset Category,Currency,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
    # Transfers,Data,Stocks,GBP,AZN,2021-10-15,Internal,In,--,U4842277,10,--,881.40,0.00,0.00,

    for sub in model.get_subtables("Transfers"):
        header = [h.strip() for h in sub.header]
        rows = sub.rows

        # We only care about stock-like transfers
        for r in rows:
            asset_cat = r.get("Asset Category", "").strip()
            if asset_cat not in ASSET_STOCK_LIKE:
                continue

            symbol = r.get("Symbol", "").strip()
            date_s = r.get("Date", "").strip()
            direction = r.get("Direction", "").strip()  # "In" or "Out"
            qty_s = r.get("Qty", "").strip()
            if not qty_s and "Quantity" in r:
                qty_s = r.get("Quantity", "").strip()

            # For incoming transfers, we need the initial cost basis.
            # Usually "Market Value" at transfer time is used if no other basis is provided,
            # BUT legally, for internal transfers, the original cost basis should persist.
            # IBKR CSV might show "Cost Basis" or "Market Value".
            # The sample CSV shows "Market Value" populated, but "Xfer Price" is "--".
            # It seems we must use Market Value as the best proxy for basis if it's an internal transfer
            # where the user didn't provide cost basis data to IBKR.
            # Or perhaps there is a "Cost Basis" column in other variants.

            # Let's try to find a value field
            val_s = r.get("Market Value", "").strip()
            if not val_s and "Cost Basis" in r:
                val_s = r.get("Cost Basis", "").strip()

            code = r.get("Code", "").strip()
            currency = r.get("Currency", "").strip()

            if not (symbol and date_s and direction and qty_s):
                raise ValueError(f"Invalid transfer row (missing fields): {r}")

            direction_norm = direction.strip().lower()
            if direction_norm not in {"in", "out"}:
                raise ValueError(
                    f"Unsupported transfer direction {direction!r} for row: {r}"
                )

            quantity = to_dec_strict(qty_s)
            if quantity <= 0:
                raise ValueError(
                    f"Transfer quantity must be positive for {symbol!r} on {date_s!r}: "
                    f"{quantity}"
                )

            # For incoming transfers, a valid basis is mandatory; treat missing/placeholder
            # Market Value / Cost Basis as a hard error.
            if direction_norm == "in":
                if not val_s:
                    raise ValueError(
                        f"Transfer IN for {symbol!r} on {date_s!r} is missing "
                        "Market Value/Cost Basis."
                    )
                market_value = to_dec_strict(val_s)
            else:
                # For OUT (or other) transfers, the market value is not used in FIFO matching.
                market_value = to_dec(val_s) if val_s else Decimal("0")

            t = TransferRow(
                section="Transfers",
                asset_category=asset_cat,
                currency=currency,
                symbol=symbol,
                date=parse_date(date_s),
                direction=direction,
                quantity=quantity,
                market_value=market_value,
                code=code,
            )
            out.append(t)

    # Sort by date
    out.sort(key=lambda x: x.date)
    return out
