"""Extract dividend cash flows from the IBKR 'Dividends' section."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import ExtractionDefect, _is_data_row, _require_date, _require_decimal

logger = logging.getLogger(__name__)


@dataclass
class DividendRow:
    currency: str
    date: dt.date
    description: str
    amount: Decimal
    amount_eur: Decimal | None = None


def parse_dividends(
    model: IbkrModel,
) -> tuple[list[DividendRow], list[ExtractionDefect]]:
    out: list[DividendRow] = []
    defects: list[ExtractionDefect] = []
    skipped_incomplete = 0
    for r in model.iter_rows("Dividends"):
        # Header: Currency,Date,Description,Amount
        cur = r.get("Currency", "").strip()
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amount_s = r.get("Amount", "").strip()
        if not _is_data_row(cur, date_s, desc):
            skipped_incomplete += 1
            continue

        try:
            amt = _require_decimal("dividend row", "Amount", amount_s)
            out.append(
                DividendRow(
                    currency=cur,
                    date=_require_date("dividend row", "Date", date_s),
                    description=desc,
                    amount=amt,
                )
            )
        except DataQualityError as e:
            defects.append(ExtractionDefect("Dividends", None, date_s or None, str(e)))

    if skipped_incomplete:
        logger.info(
            "Dividends: skipped %d incomplete row(s) "
            "(missing currency/date/description)",
            skipped_incomplete,
        )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d dividend entries", len(out))
    return out, defects
