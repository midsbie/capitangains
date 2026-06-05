"""Extract credit/debit and SYEP-summary interest from the IBKR 'Interest' section."""

from __future__ import annotations

import datetime as dt
import logging
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

logger = logging.getLogger(__name__)


@dataclass
class InterestRow:
    currency: str
    date: dt.date
    description: str
    amount: Decimal
    amount_eur: Decimal | None = None


def _build_interest_row(f: CashFlowFields) -> InterestRow:
    amt = _require_decimal("interest row", "Amount", f.amount_s)
    return InterestRow(
        currency=f.currency,
        date=_require_date("interest row", "Date", f.date_s),
        description=f.description,
        amount=amt,
    )


def parse_interest(
    model: IbkrModel,
) -> tuple[list[InterestRow], list[ExtractionDefect]]:
    """Parse 'Interest' section: credit/debit interest and monthly SYEP interest
    summaries.

    Header: Currency, Date, Description, Amount

    Excludes CSV total rows (e.g., 'Total', 'Total in EUR').
    """
    out, defects = _extract_cashflow_section(
        model,
        section="Interest",
        logger=logger,
        build=_build_interest_row,
        incomplete_label="Interest",
        incomplete_detail="missing date/description",
        skip_totals=True,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d interest entries", len(out))
    return out, defects
