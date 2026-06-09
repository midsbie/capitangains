"""Extract the SYEP securities-lent interest detail rows."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from ._common import (
    ExtractionDefect,
    _is_total_or_empty,
    _require_date,
    _require_decimal,
)
from .sections import SEC_SYEP

logger = logging.getLogger(__name__)


@dataclass
class SyepInterestRow:
    currency: str
    value_date: dt.date | None
    symbol: str
    start_date: dt.date | None
    quantity: Decimal
    collateral_amount: Decimal
    market_rate_pct: Decimal
    customer_rate_pct: Decimal
    interest_paid: Decimal
    code: str
    interest_paid_eur: Decimal | None = None


def parse_syep_interest_details(
    model: IbkrModel,
) -> tuple[list[SyepInterestRow], list[ExtractionDefect]]:
    """Parse 'Stock Yield Enhancement Program Securities Lent Interest Details'.

    Expected header:
      Currency, Value Date, Symbol, Start Date, Quantity, Collateral Amount,
      Market-based Rate (%), Interest Rate on Customer Collateral (%),
      Interest Paid to Customer, Code
    """
    out: list[SyepInterestRow] = []
    defects: list[ExtractionDefect] = []
    section = SEC_SYEP
    for r in model.iter_rows(section):
        cur = r.get("Currency", "").strip()
        value_date_s = r.get("Value Date", "").strip()
        sym = r.get("Symbol", "").strip()
        start_date_s = r.get("Start Date", "").strip()
        qty_s = r.get("Quantity", "").strip()
        collat_s = r.get("Collateral Amount", "").strip()
        mkt_rate_s = r.get("Market-based Rate (%)", "").strip()
        cust_rate_s = r.get("Interest Rate on Customer Collateral (%)", "").strip()
        paid_s = r.get("Interest Paid to Customer", "").strip()
        code = r.get("Code", "").strip()

        # Skip trailing totals like 'Total', 'Total in EUR'.
        if _is_total_or_empty(cur):
            continue

        try:
            if not (qty_s and collat_s and mkt_rate_s and cust_rate_s and paid_s):
                raise DataQualityError(
                    f"Invalid SYEP interest row (missing numeric fields): {r}"
                )

            quantity = _require_decimal("SYEP interest row", "Quantity", qty_s)
            collateral_amount = _require_decimal(
                "SYEP interest row", "Collateral Amount", collat_s
            )
            market_rate_pct = _require_decimal(
                "SYEP interest row", "Market-based Rate (%)", mkt_rate_s
            )
            customer_rate_pct = _require_decimal(
                "SYEP interest row",
                "Interest Rate on Customer Collateral (%)",
                cust_rate_s,
            )
            interest_paid = _require_decimal(
                "SYEP interest row", "Interest Paid to Customer", paid_s
            )

            out.append(
                SyepInterestRow(
                    currency=cur,
                    value_date=(
                        _require_date("SYEP interest row", "Value Date", value_date_s)
                        if value_date_s
                        else None
                    ),
                    symbol=sym,
                    start_date=(
                        _require_date("SYEP interest row", "Start Date", start_date_s)
                        if start_date_s
                        else None
                    ),
                    quantity=quantity,
                    collateral_amount=collateral_amount,
                    market_rate_pct=market_rate_pct,
                    customer_rate_pct=customer_rate_pct,
                    interest_paid=interest_paid,
                    code=code,
                )
            )
        except DataQualityError as e:
            defects.append(
                ExtractionDefect(
                    "SYEP Interest", sym or None, value_date_s or None, str(e)
                )
            )

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d SYEP interest entries", len(out))
    return out, defects
