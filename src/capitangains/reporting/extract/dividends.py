"""Extract dividend cash flows from the IBKR 'Dividends' section."""

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
class DividendRow:
    currency: str
    date: dt.date
    description: str
    amount: Decimal
    amount_eur: Decimal | None = None


def _build_dividend_row(f: CashFlowFields) -> DividendRow:
    amt = _require_decimal("dividend row", "Amount", f.amount_s)
    return DividendRow(
        currency=f.currency,
        date=_require_date("dividend row", "Date", f.date_s),
        description=f.description,
        amount=amt,
    )


def parse_dividends(
    model: IbkrModel,
) -> tuple[list[DividendRow], list[ExtractionDefect]]:
    out, defects = _extract_cashflow_section(
        model,
        section="Dividends",
        logger=logger,
        build=_build_dividend_row,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d dividend entries", len(out))
    return out, defects
