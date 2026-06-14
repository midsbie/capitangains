"""Shared test-support layer: one canonical builder per concept.

Single import surface: ``from tests.support import parse_model, trade_row, ...``.
- builders.py  -- domain objects (the real production dataclasses)
- statement_rows.py -- raw CSV-wire rows (columns, assemblers, writer)
- doubles.py  -- minimal protocol doubles (Trade, Transfer)
"""

from __future__ import annotations

from .builders import (
    buy,
    convert,
    ingest,
    make_fx,
    make_gap_event,
    make_matcher,
    parse_model,
    realized_line,
    sell,
    sell_match_leg,
    trade_row,
    transfer_row,
)
from .doubles import Trade, Transfer
from .statement_rows import (
    ACCOUNT_COLUMNS,
    DIVIDENDS_COLUMNS,
    INTEREST_COLUMNS,
    STATEMENT_COLUMNS,
    SYEP_COLUMNS,
    SYEP_SECTION,
    TRADES_COLUMNS,
    TRANSFERS_COLUMNS,
    WITHHOLDING_COLUMNS,
    Y2023,
    Y2024,
    header_row,
    section_table,
    statement_meta_rows,
    trade_data,
    transfer_data,
    write_statement_csv,
)

__all__ = [
    # builders.py
    "buy",
    "convert",
    "ingest",
    "make_fx",
    "make_gap_event",
    "make_matcher",
    "parse_model",
    "realized_line",
    "sell",
    "sell_match_leg",
    "trade_row",
    "transfer_row",
    # doubles.py
    "Trade",
    "Transfer",
    # statement_rows.py
    "ACCOUNT_COLUMNS",
    "DIVIDENDS_COLUMNS",
    "INTEREST_COLUMNS",
    "STATEMENT_COLUMNS",
    "SYEP_COLUMNS",
    "SYEP_SECTION",
    "TRADES_COLUMNS",
    "TRANSFERS_COLUMNS",
    "WITHHOLDING_COLUMNS",
    "Y2023",
    "Y2024",
    "header_row",
    "section_table",
    "statement_meta_rows",
    "trade_data",
    "transfer_data",
    "write_statement_csv",
]
