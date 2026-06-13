"""Extractors for IBKR statement sections.

Most submodules own one section's row dataclass and its pure model -> (rows, defects)
extractor; statement owns the singular statement-level metadata (account and reporting
period). Shared parsing helpers and the ExtractionDefect record live in _common. The
public surface is re-exported here so callers keep importing from
capitangains.reporting.extract.
"""

from ._common import CashFlowRow, ExtractionDefect
from .dividends import DividendRow, parse_dividends
from .interest import InterestRow, parse_interest
from .statement import StatementMetadata, StatementPeriod, parse_statement_metadata
from .syep import SyepInterestRow, parse_syep_interest_details
from .trades import AssetScope, TradeRow, parse_trades_stocklike
from .transfers import TransferRow, parse_transfers
from .withholding import WithholdingRow, parse_withholding_tax

__all__ = [
    "AssetScope",
    "ExtractionDefect",
    "TradeRow",
    "TransferRow",
    "CashFlowRow",
    "DividendRow",
    "InterestRow",
    "WithholdingRow",
    "SyepInterestRow",
    "StatementPeriod",
    "StatementMetadata",
    "parse_trades_stocklike",
    "parse_transfers",
    "parse_dividends",
    "parse_withholding_tax",
    "parse_syep_interest_details",
    "parse_interest",
    "parse_statement_metadata",
]
