"""Extract credit/debit and SYEP-summary interest from the IBKR 'Interest' section."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import (
    ExtractionDefect,
    _is_data_row,
    _is_total_or_empty,
    _require_date,
    _require_decimal,
)

logger = logging.getLogger(__name__)


@dataclass
class InterestRow:
    currency: str
    date: dt.date
    description: str
    amount: Decimal
    amount_eur: Decimal | None = None


def parse_interest(
    model: IbkrModel,
) -> tuple[list[InterestRow], list[ExtractionDefect]]:
    """Parse 'Interest' section: credit/debit interest and monthly SYEP interest
    summaries.

    Header: Currency, Date, Description, Amount

    Excludes CSV total rows (e.g., 'Total', 'Total in EUR').

    """
    out: list[InterestRow] = []
    defects: list[ExtractionDefect] = []
    skipped_incomplete = 0
    for r in model.iter_rows("Interest"):
        cur = r.get("Currency", "").strip()
        if _is_total_or_empty(cur):
            continue
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amount_s = r.get("Amount", "").strip()
        # If we ever decide a partial interest row is an invariant violation, the
        # correct response would be to raise here, not to skip-and-count.
        if _is_data_row(cur, date_s, desc):
            try:
                amt = _require_decimal("interest row", "Amount", amount_s)
                out.append(
                    InterestRow(
                        currency=cur,
                        date=_require_date("interest row", "Date", date_s),
                        description=desc,
                        amount=amt,
                    )
                )
            except DataQualityError as e:
                defects.append(
                    ExtractionDefect("Interest", None, date_s or None, str(e))
                )
        else:
            skipped_incomplete += 1

    if skipped_incomplete:
        logger.info(
            "Interest: skipped %d incomplete row(s) (missing date/description)",
            skipped_incomplete,
        )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d interest entries", len(out))
    return out, defects
