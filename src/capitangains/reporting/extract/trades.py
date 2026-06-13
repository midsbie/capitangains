"""Extract stock-like trades from the IBKR 'Trades' section."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from capitangains.conv import Currency
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import (
    ExtractionDefect,
    _optional_decimal,
    _require_date,
    _require_decimal,
    _require_fields,
)
from .sections import SEC_TRADES

logger = logging.getLogger(__name__)

# Accepted asset-category filters. Kept in lockstep with ALL_SCOPES_SET (its keys): a
# new scope must be added to both, and mypy flags a key the Literal does not list.
AssetScope = Literal["stocks", "etfs", "stocks_etfs", "all"]

_STOCKS = {"Stocks", "Stock"}
_ETFS = {"ETF", "ETFs", "ETCs", "ETP"}
ALL_SCOPES_SET: dict[AssetScope, set[str] | None] = {
    "stocks": _STOCKS,
    "etfs": _ETFS,
    "stocks_etfs": _STOCKS | _ETFS,
    "all": None,
}

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
    currency: Currency
    symbol: str
    datetime_str: str
    date: dt.date
    quantity: Decimal  # positive buy, negative sell
    t_price: Decimal  # mandatory per IBKR spec; strict gate, not read by valuation
    proceeds: Decimal  # signed: negative buy cash, positive sell cash
    comm_fee: Decimal  # signed: negative fee/commission, rarely positive rebates
    code: str
    basis_ccy: Decimal | None = None  # signed; sells are negative
    realized_pl_ccy: Decimal | None = None


def parse_trades_stocklike_row(
    scope_set: set[str] | None, r: dict[str, str]
) -> TradeRow | None:
    asset_category = r.get("Asset Category", "").strip()
    if scope_set is not None and asset_category not in scope_set:
        return None

    currency = r.get("Currency", "").strip()
    symbol = r.get("Symbol", "").strip()
    _require_fields("trade row", symbol=symbol, currency=currency)
    dt_str = r.get("Date/Time", "").strip()
    qty_s = r.get("Quantity", "").strip()
    proceeds_s = r.get("Proceeds", "").strip()
    code = r.get("Code", "").strip()

    t_price_s = r.get("T. Price", "").strip()

    # Commission is 'Comm/Fee' in stock trades, 'Comm in EUR' in some Forex tables;
    # track which label resolved so a strict-parse failure names it. Parsed strictly:
    # commission feeds basis on buys and net proceeds on sells, so a silently-zeroed
    # placeholder would bias the filed gain (a commission-free trade reports '0').
    comm_col = (
        "Comm in EUR" if ("Comm in EUR" in r and "Comm/Fee" not in r) else "Comm/Fee"
    )
    comm_s = r.get(comm_col, "").strip()

    # Strict vs optional mirrors the IBKR Trades spec (doc/IBKR-reporting_guide.txt).
    # Quantity, T. Price, Proceeds and Comm/Fee are mandatory on every row (Stocks and
    # Forex), so a blank fails closed; do NOT relax T. Price just because valuation
    # ignores it. Basis and Realized P/L are the only optional columns: IBKR blanks them
    # on Forex rows, so only those map elision to None via _optional_decimal (a
    # malformed cell there is still rejected as a defect).
    trade = TradeRow(
        section=SEC_TRADES,
        asset_category=asset_category,
        currency=Currency(currency),
        symbol=symbol,
        datetime_str=dt_str,
        date=_require_date("trade row", "Date/Time", dt_str),
        quantity=_require_decimal("trade row", "Quantity", qty_s),
        t_price=_require_decimal("trade row", "T. Price", t_price_s),
        proceeds=_require_decimal("trade row", "Proceeds", proceeds_s),
        comm_fee=_require_decimal("trade row", comm_col, comm_s),
        code=code,
        basis_ccy=_optional_decimal("trade row", "Basis", r.get("Basis")),
        realized_pl_ccy=_optional_decimal(
            "trade row", "Realized P/L", r.get("Realized P/L")
        ),
    )

    return trade if trade.quantity != 0 else None


def parse_trades_stocklike(
    model: IbkrModel, asset_scope: AssetScope = "stocks"
) -> tuple[list[TradeRow], list[ExtractionDefect]]:
    """Extract stock-like trades from 'Trades' section across header variants.
    asset_scope: 'stocks', 'etfs', 'stocks_etfs', 'all'

    Returns the extracted rows and a list of row-level defects (empty on clean input);
    the caller halts at the boundary if any defect is present.
    """
    scope_set = ALL_SCOPES_SET[asset_scope]
    trades: list[TradeRow] = []
    defects: list[ExtractionDefect] = []
    skipped_rows = 0

    for sub in model.get_subtables(SEC_TRADES):
        header = [h.strip() for h in sub.header]
        rows = sub.rows

        if logger.isEnabledFor(logging.DEBUG):
            asset_categories = {
                r.get("Asset Category", "") for r in rows if r.get("Asset Category")
            }
            logger.debug(
                "Processing Trades subtable with %d rows (Asset Categories: %s)",
                len(rows),
                asset_categories,
            )

        col: dict[str, int | None] = {k: None for k in TRADE_COLS}
        for name in col:
            for i, h in enumerate(header):
                if h == name:
                    col[name] = i
                    break

        # Skip subtables without essential columns. Losing a whole subtable is material,
        # so warn (default-visible) rather than logging at debug.
        missing_cols = [n for n in NEED_TRADE_COLS if col[n] is None]
        if missing_cols:
            logger.warning(
                "Skipping Trades subtable: missing required column(s) %s", missing_cols
            )
            continue

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Mapped Trades columns: %s",
                {k: header[v] for k, v in col.items() if v is not None},
            )

        for r in rows:
            # Per-row granularity: the _require_* helpers raise on the first bad field,
            # so a defect names that field; multi-defect rows are rare. The benign None
            # return (out-of-scope / zero-qty) is unchanged -- only a defect is caught.
            try:
                trade = parse_trades_stocklike_row(scope_set, r)
            except DataQualityError as e:
                defects.append(
                    ExtractionDefect(
                        SEC_TRADES,
                        r.get("Symbol", "").strip() or None,
                        r.get("Date/Time", "").strip() or None,
                        str(e),
                    )
                )
                continue

            if trade is not None:
                trades.append(trade)
            else:
                skipped_rows += 1

    # Intentionally unsorted: ordering is the pipeline's job. It merges trades
    # with transfers and orders the combined stream in EventStream, the single source of
    # ordering truth for FIFO, so pre-sorting here would be moot.
    if skipped_rows:
        logger.info(
            "Trades (scope=%r): skipped %d row(s) -- out-of-scope asset category "
            "or zero quantity",
            asset_scope,
            skipped_rows,
        )

    elided_basis = sum(1 for t in trades if t.basis_ccy is None)
    elided_realized = sum(1 for t in trades if t.realized_pl_ccy is None)
    if elided_basis or elided_realized:
        logger.info(
            "Trades: %d row(s) with elided Basis, %d with elided Realized P/L "
            "(gap synthesis may be affected)",
            elided_basis,
            elided_realized,
        )

    if logger.isEnabledFor(logging.DEBUG):
        buys = sum(1 for t in trades if t.quantity > 0)
        sells = sum(1 for t in trades if t.quantity < 0)
        logger.debug(
            "Extracted %d trades (%d buys, %d sells)",
            len(trades),
            buys,
            sells,
        )

    return trades, defects
