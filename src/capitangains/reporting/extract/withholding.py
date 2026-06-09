"""Extract and classify withholding tax from the IBKR 'Withholding Tax' section."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from capitangains.model import IbkrModel

from ._common import (
    CashFlowFields,
    ExtractionDefect,
    _extract_cashflow_section,
    _require_date,
    _require_decimal,
)
from .sections import SEC_WITHHOLDING

logger = logging.getLogger(__name__)


@dataclass
class WithholdingRow:
    currency: str
    date: dt.date
    description: str
    amount: Decimal
    code: str
    type: str
    country: str
    amount_eur: Decimal | None = None


def _build_withholding_row(f: CashFlowFields) -> WithholdingRow:
    amt = _require_decimal("withholding tax row", "Amount", f.amount_s)
    code = f.raw.get("Code", "").strip()

    dlow = f.description.lower()
    # Classify withholding tax type with explicit precedence
    # Most specific patterns first, then generic fallbacks
    if "credit interest" in dlow:
        wtype = "Interest"
    elif "dividend" in dlow:
        # Catches "cash dividend", "payment in lieu of dividend", "interest
        # dividend", etc.; dividend takes precedence
        wtype = "Dividend"
    elif "interest" in dlow:
        # Generic interest (not dividend-related, already caught above)
        wtype = "Interest"
    else:
        # Unknown/other
        logger.warning(
            "Unrecognized withholding tax description: %r. "
            "Classifying as 'Unknown'. Please verify data integrity.",
            f.description,
        )
        wtype = "Unknown"

    # Extract country from suffix like " - US Tax" or " - NL Tax"
    country = ""
    m = re.search(r"-\s+([A-Z]{2})\s+Tax\b", f.description)
    if m:
        country = m.group(1)

    return WithholdingRow(
        currency=f.currency,
        date=_require_date("withholding tax row", "Date", f.date_s),
        description=f.description,
        amount=amt,
        code=code,
        type=wtype,
        country=country,
    )


def parse_withholding_tax(
    model: IbkrModel,
) -> tuple[list[WithholdingRow], list[ExtractionDefect]]:
    out, defects = _extract_cashflow_section(
        model,
        section=SEC_WITHHOLDING,
        logger=logger,
        build=_build_withholding_row,
    )
    if logger.isEnabledFor(logging.DEBUG):
        counts = Counter(w.type for w in out)
        summary = ", ".join(f"{wtype}: {count}" for wtype, count in counts.items())
        logger.debug(
            "Extracted %d withholding tax entries (%s)",
            len(out),
            summary or "none",
        )
    return out, defects
