from __future__ import annotations

import logging
import re
from collections import Counter
from decimal import Decimal

from capitangains.conv import to_dec_strict
from capitangains.model import IbkrModel

from .extract import ASSET_STOCK_LIKE

logger = logging.getLogger(__name__)

# Fallback symbol-column index for summary subtables that omit a Symbol/Ticker/
# Description header — the position it commonly occupies in IBKR's layout.
_SYMBOL_FALLBACK_IDX = 2


def _report_skips(label: str, counts: Counter[str]) -> None:
    """Emit one aggregate INFO line for row-level skips, omitting zero-count reasons.

    Reasons are tallied during the per-row loop and reported once here, so a subtable
    with many skips produces a single line rather than per-row noise. Nothing is logged
    when no rows were skipped. Reasons retain their seeded order for stable output.
    """
    total = sum(counts.values())
    if not total:
        return
    breakdown = ", ".join(f"{n} {reason}" for reason, n in counts.items() if n)
    logger.info("%s: skipped %d row(s) (%s)", label, total, breakdown)


def reconcile_with_ibkr_summary(model: IbkrModel) -> dict[str, Decimal]:
    """Try to read 'Realized & Unrealized Performance Summary' for Stocks.

    Returns map: symbol -> realized_eur.
    If parsing fails (sanitized CSV), returns empty dict.
    """
    result: dict[str, Decimal] = {}
    # Seeded with zero so the breakdown keeps a stable, documented reason order.
    skips: Counter[str] = Counter(
        {"non-stock": 0, "empty symbol": 0, "no numeric value": 0}
    )
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
            if len(header) > _SYMBOL_FALLBACK_IDX:
                # No recognized symbol column, but the common-layout fallback exists.
                # Guess it and warn — it can mis-key every row; correctness fix tracked
                # separately.
                logger.warning(
                    "Reconciliation: no Symbol/Ticker/Description column in header %s; "
                    "falling back to column %d (%r) — reconciliation keys may be wrong",
                    header,
                    _SYMBOL_FALLBACK_IDX,
                    header[_SYMBOL_FALLBACK_IDX],
                )
                idx_symbol = _SYMBOL_FALLBACK_IDX
            else:
                # Not even the fallback column exists: no usable symbol. Skip the
                # subtable (default-visible) rather than keying every row off nothing.
                logger.warning(
                    "Reconciliation: header %s has no usable symbol column; "
                    "skipping subtable",
                    header,
                )
                continue

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
                skips["non-stock"] += 1
                continue
            sym = r.get(header[idx_symbol], "").strip()
            if not sym:
                skips["empty symbol"] += 1
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
            else:
                skips["no numeric value"] += 1

    _report_skips("Reconciliation", skips)
    logger.debug("Reconciliation parsed %d symbols from IBKR summary", len(result))
    return result
