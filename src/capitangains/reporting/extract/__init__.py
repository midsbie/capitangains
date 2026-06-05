"""Row extractors for IBKR statement sections.

Each submodule owns one section's row dataclass and its pure
``model -> (rows, defects)`` extractor; shared parsing helpers and the
``ExtractionDefect`` record live in ``_common``. The public surface is re-exported
here so callers keep importing from ``capitangains.reporting.extract``.
"""

from ._common import ExtractionDefect
from .dividends import DividendRow, parse_dividends
from .interest import InterestRow, parse_interest
from .syep import SyepInterestRow, parse_syep_interest_details
from .trades import TradeRow, parse_trades_stocklike
from .transfers import TransferRow, parse_transfers
from .withholding import WithholdingRow, parse_withholding_tax

__all__ = [
    "ExtractionDefect",
    "TradeRow",
    "TransferRow",
    "DividendRow",
    "WithholdingRow",
    "InterestRow",
    "SyepInterestRow",
    "parse_trades_stocklike",
    "parse_transfers",
    "parse_dividends",
    "parse_withholding_tax",
    "parse_syep_interest_details",
    "parse_interest",
]
