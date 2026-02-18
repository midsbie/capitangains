from __future__ import annotations

import logging
import re
from decimal import Decimal

from capitangains.conv import to_dec_strict
from capitangains.model import IbkrModel

from .extract import ASSET_STOCK_LIKE

logger = logging.getLogger(__name__)


def reconcile_with_ibkr_summary(model: IbkrModel) -> dict[str, Decimal]:
    """Try to read 'Realized & Unrealized Performance Summary' for Stocks.

    Returns map: symbol -> realized_eur.
    If parsing fails (sanitized CSV), returns empty dict.
    """
    result: dict[str, Decimal] = {}
    for sub in model.get_subtables("Realized & Unrealized Performance Summary"):
        header = [h.strip() for h in sub.header]
        rows = sub.rows

        logger.debug(
            "Found 'Realized & Unrealized Performance Summary' subtable with %d rows",
            len(rows),
        )

        # Heuristic: Find columns for Asset Category, Symbol, Total (or Realized Total).
        # In many IBKR statements, columns include fields for realized/unrealized P/L
        # and a final "Total".
        try:
            header.index("Asset Category")
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

        # Try to find a realized EUR column. Heuristic: pick the last numeric column.
        # Because in some sanitized exports values are elided with "...", we may fail.
        numeric_cols = [
            i
            for i, h in enumerate(header)
            if re.search(r"(Total|Realized|P/L|Profit|Loss)", h, re.I)
        ]
        candidate_cols = numeric_cols or list(
            range(max(0, len(header) - 10), len(header))
        )

        logger.debug(
            "Reconciliation column mapping: symbol_idx=%s, candidate_cols=%s",
            idx_symbol,
            candidate_cols,
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
            found_col: int | None = None
            for ci in reversed(candidate_cols):
                v = r.get(header[ci], "")
                try:
                    val = to_dec_strict(v)
                    found_col = ci
                    break
                except ValueError:
                    continue

            if val is not None and found_col is not None:
                logger.debug(
                    "Reconciliation extracted: %s: %s EUR (from col %d: %s)",
                    sym,
                    val,
                    found_col,
                    header[found_col],
                )
                result[sym] = result.get(sym, Decimal("0")) + val

    logger.debug("Reconciliation parsed %d symbols from IBKR summary", len(result))
    return result
