"""Extract and classify withholding tax from the IBKR 'Withholding Tax' section."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import ExtractionDefect, _is_data_row, _require_date, _require_decimal

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


def parse_withholding_tax(
    model: IbkrModel,
) -> tuple[list[WithholdingRow], list[ExtractionDefect]]:
    out: list[WithholdingRow] = []
    defects: list[ExtractionDefect] = []
    skipped_incomplete = 0
    for r in model.iter_rows("Withholding Tax"):
        cur = r.get("Currency", "").strip()
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amount_s = r.get("Amount", "").strip()
        code = r.get("Code", "").strip() if "Code" in r else ""

        if not _is_data_row(cur, date_s, desc):
            skipped_incomplete += 1
            continue

        try:
            amt = _require_decimal("withholding tax row", "Amount", amount_s)
            dlow = desc.lower()
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
                    desc,
                )
                wtype = "Unknown"

            # Extract country from suffix like " - US Tax" or " - NL Tax"
            country = ""
            m = re.search(r"-\s+([A-Z]{2})\s+Tax\b", desc)
            if m:
                country = m.group(1)

            out.append(
                WithholdingRow(
                    currency=cur,
                    date=_require_date("withholding tax row", "Date", date_s),
                    description=desc,
                    amount=amt,
                    code=code,
                    type=wtype,
                    country=country,
                )
            )
        except DataQualityError as e:
            defects.append(
                ExtractionDefect("Withholding Tax", None, date_s or None, str(e))
            )

    if skipped_incomplete:
        logger.info(
            "Withholding tax: skipped %d incomplete row(s) "
            "(missing currency/date/description)",
            skipped_incomplete,
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
