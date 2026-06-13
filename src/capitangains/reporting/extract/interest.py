"""Extract credit/debit and SYEP-summary interest from the IBKR 'Interest' section."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from capitangains.model import IbkrModel

from ._common import (
    CashFlowRow,
    ExtractionDefect,
    _extract_cashflow_section,
)
from .sections import SEC_INTEREST

logger = logging.getLogger(__name__)


@dataclass
class InterestRow(CashFlowRow):
    """An account-interest cash flow, reported under the 'interest row' label."""

    _label: ClassVar[str] = "interest row"


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
        section=SEC_INTEREST,
        logger=logger,
        build=InterestRow.from_fields,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d interest entries", len(out))
    return out, defects
