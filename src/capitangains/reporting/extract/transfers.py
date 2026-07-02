"""Extract stock-like position transfers from the IBKR 'Transfers' section."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.conv.ibkr import to_dec
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import (
    ASSET_STOCK_LIKE,
    ExtractionDefect,
    _require_date,
    _require_decimal,
    _require_fields,
    defect_from_row,
)
from .sections import SEC_TRANSFERS

logger = logging.getLogger(__name__)


@dataclass
class TransferRow:
    section: str
    asset_category: str
    currency: Currency
    symbol: str
    date: dt.date
    direction: str  # "In" or "Out"
    quantity: Decimal
    # Market value at transfer date; the fallback basis used only when the Open
    # Positions cost basis cannot be resolved for this lot (see
    # reporting.transfer_basis).
    market_value: Decimal
    code: str
    # The lot's true cost basis, filled in place from the Open Positions section after
    # parsing (see reporting.transfer_basis). None until resolved, and None when no
    # snapshot matches.
    cost_basis: Decimal | None = None


def parse_transfers(
    model: IbkrModel,
) -> tuple[list[TransferRow], list[ExtractionDefect]]:
    """Extract stock transfers from IBKR 'Transfers' section.

    Assumptions / invariants:
    - Only stock-like asset categories are considered (ASSET_STOCK_LIKE).
    - Direction must be 'In' or 'Out' (case-insensitive); other values are rejected.
    - Quantity must be strictly positive; zero/negative quantities are treated as
      errors.
    - For 'In' transfers, Market Value / Cost Basis must be present and parseable;
      missing or placeholder basis is treated as an error.
    - Transfers feed FIFO interleaved with trades by date (not strictly before them):
      an 'In' seeds a lot and an 'Out' consumes lots.
    - Market Value at transfer date is a fallback basis only; an 'In' lot's true basis
      is resolved later from Open Positions (see reporting.transfer_basis).
    """
    out: list[TransferRow] = []
    defects: list[ExtractionDefect] = []
    skipped_non_stock = 0

    for sub in model.get_subtables(SEC_TRANSFERS):
        rows = sub.rows

        # Column-variant fallbacks are applied per row below; surface which alternate a
        # subtable relies on once, from its header, rather than per row.
        sub_header = set(sub.header)
        if "Qty" not in sub_header and "Quantity" in sub_header:
            logger.info("Transfers subtable: using 'Quantity' column (no 'Qty')")
        if "Market Value" not in sub_header and "Cost Basis" in sub_header:
            logger.info(
                "Transfers subtable: using 'Cost Basis' column (no 'Market Value') "
                "as basis"
            )

        for r in rows:
            asset_cat = r.get("Asset Category", "").strip()
            if asset_cat not in ASSET_STOCK_LIKE:
                skipped_non_stock += 1
                continue

            symbol = r.get("Symbol", "").strip()
            date_s = r.get("Date", "").strip()
            direction = r.get("Direction", "").strip()
            qty_s = r.get("Qty", "").strip()
            if not qty_s and "Quantity" in r:
                qty_s = r.get("Quantity", "").strip()

            # Read Market Value (the fallback basis; the true basis is resolved later
            # from Open Positions). Fall back to a "Cost Basis" column when a CSV
            # variant exposes that instead of "Market Value".
            val_s = r.get("Market Value", "").strip()
            if not val_s and "Cost Basis" in r:
                val_s = r.get("Cost Basis", "").strip()

            code = r.get("Code", "").strip()
            currency = r.get("Currency", "").strip()

            try:
                _require_fields(
                    "transfer row",
                    symbol=symbol,
                    date=date_s,
                    direction=direction,
                    quantity=qty_s,
                    currency=currency,
                )

                direction_norm = direction.lower()
                if direction_norm not in {"in", "out"}:
                    raise DataQualityError(
                        f"Unsupported transfer direction {direction!r} for row: {r}"
                    )

                quantity = _require_decimal("transfer row", "Quantity", qty_s)
                if quantity <= 0:
                    raise DataQualityError(
                        f"Transfer quantity must be positive for {symbol!r} on "
                        f"{date_s!r}: {quantity}"
                    )

                if direction_norm == "in":
                    if not val_s:
                        raise DataQualityError(
                            f"Transfer IN for {symbol!r} on {date_s!r} is missing "
                            "Market Value/Cost Basis."
                        )
                    market_value = _require_decimal(
                        "transfer row", "Market Value/Cost Basis", val_s
                    )
                else:
                    # OUT (or other) transfers: market_value is parsed but never
                    # consumed downstream -- fifo.ingest_transfer's OUT branch only
                    # consumes lots by quantity and returns no RealizedLine, so a
                    # lenient parse here cannot bias any tax figure, and IBKR
                    # legitimately emits "--" for this cell. If OUT ever consumes basis,
                    # make this strict.
                    market_value = to_dec(val_s)

                out.append(
                    TransferRow(
                        section=SEC_TRANSFERS,
                        asset_category=asset_cat,
                        currency=Currency(currency),
                        symbol=symbol,
                        date=_require_date("transfer row", "Date", date_s),
                        direction=direction,
                        quantity=quantity,
                        market_value=market_value,
                        code=code,
                    )
                )
            except DataQualityError as e:
                defects.append(defect_from_row(SEC_TRANSFERS, r, str(e)))
                continue

    out.sort(key=lambda x: x.date)

    if skipped_non_stock:
        logger.info("Transfers: skipped %d non-stock row(s)", skipped_non_stock)

    if logger.isEnabledFor(logging.DEBUG):
        ins = sum(1 for t in out if t.direction.lower() == "in")
        outs = sum(1 for t in out if t.direction.lower() == "out")
        logger.debug(
            "Extracted %d transfers (%d IN, %d OUT)",
            len(out),
            ins,
            outs,
        )

    return out, defects
