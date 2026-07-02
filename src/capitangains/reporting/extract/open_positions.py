"""Extract per-symbol cost basis from the IBKR 'Open Positions' section.

The Open Positions 'Cost Basis' column is what IBKR actually paid for a held lot, unlike
the Transfers 'Market Value' (the transfer-day price). It is the authoritative basis for
a transferred-in lot; reporting.transfer_basis matches a transfer to its snapshot here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import (
    ASSET_STOCK_LIKE,
    ExtractionDefect,
    _require_decimal,
    _require_fields,
    defect_from_row,
)
from .sections import SEC_OPEN_POSITIONS

logger = logging.getLogger(__name__)

# IBKR tags each Open Positions data row in DataDiscriminator: a per-symbol 'Summary'
# and, in some exports, per-lot 'Lot' breakdowns that sum to it. Only the Summary
# carries the whole position's Cost Basis, so the per-lot rows are skipped to avoid
# double-counting.
_SUMMARY_DISCRIMINATOR = "Summary"


@dataclass(frozen=True)
class OpenPositionRow:
    symbol: str
    currency: Currency
    quantity: Decimal
    cost_basis: Decimal


def parse_open_positions(
    model: IbkrModel,
) -> tuple[list[OpenPositionRow], list[ExtractionDefect]]:
    """Extract stock-like position snapshots from the IBKR 'Open Positions' section.

    Keeps only per-symbol Summary rows of a stock-like category; other rows are skipped,
    not rejected. All four fields are required and parsed strictly: a snapshot with a
    blank or malformed cell cannot supply the basis it exists for. Collects per-row
    defects rather than raising; the boundary only WARNs on them (see
    diagnostics.report_open_position_defects).
    """
    out: list[OpenPositionRow] = []
    defects: list[ExtractionDefect] = []
    skipped = 0

    for sub in model.get_subtables(SEC_OPEN_POSITIONS):
        for r in sub.rows:
            if r.get("DataDiscriminator", "").strip() != _SUMMARY_DISCRIMINATOR:
                skipped += 1
                continue
            if r.get("Asset Category", "").strip() not in ASSET_STOCK_LIKE:
                skipped += 1
                continue

            symbol = r.get("Symbol", "").strip()
            currency = r.get("Currency", "").strip()
            qty_s = r.get("Quantity", "").strip()
            basis_s = r.get("Cost Basis", "").strip()

            try:
                _require_fields(
                    "open position row",
                    symbol=symbol,
                    currency=currency,
                    quantity=qty_s,
                    cost_basis=basis_s,
                )
                out.append(
                    OpenPositionRow(
                        symbol=symbol,
                        currency=Currency(currency),
                        quantity=_require_decimal(
                            "open position row", "Quantity", qty_s
                        ),
                        cost_basis=_require_decimal(
                            "open position row", "Cost Basis", basis_s
                        ),
                    )
                )
            except DataQualityError as e:
                defects.append(defect_from_row(SEC_OPEN_POSITIONS, r, str(e)))
                continue

    if skipped:
        logger.info("Open Positions: skipped %d non-summary/non-stock row(s)", skipped)

    return out, defects
