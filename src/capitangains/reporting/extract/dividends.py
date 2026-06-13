"""Extract dividend cash flows from the IBKR 'Dividends' section."""

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
from .sections import SEC_DIVIDENDS

logger = logging.getLogger(__name__)


@dataclass
class DividendRow(CashFlowRow):
    """A dividend cash flow, reported under the 'dividend row' label."""

    _label: ClassVar[str] = "dividend row"


def parse_dividends(
    model: IbkrModel,
) -> tuple[list[DividendRow], list[ExtractionDefect]]:
    out, defects = _extract_cashflow_section(
        model,
        section=SEC_DIVIDENDS,
        logger=logger,
        build=DividendRow.from_fields,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Extracted %d dividend entries", len(out))
    return out, defects
