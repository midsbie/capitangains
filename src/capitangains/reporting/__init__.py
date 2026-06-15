from .event_stream import EventStream
from .extract import (
    ExtractionDefect,
    StatementMetadata,
    StatementPeriod,
    TradeRow,
    TransferRow,
    parse_dividends,
    parse_interest,
    parse_statement_metadata,
    parse_syep_interest_details,
    parse_trades_stocklike,
    parse_transfers,
    parse_withholding_tax,
)
from .fifo import FifoMatcher, Lot, RealizedLine
from .fx import FxTable
from .quadro_8a import Quadro8ALine
from .reconcile import (
    ReconciliationReport,
    SymbolReconciliation,
    reconcile_realized_against_ibkr,
)
from .report_builder import ReportBuilder
from .report_sink import ExcelReportSink
from .source import IbkrActivityStatementSource, ParsedStatement
from .validation import (
    OrderingCollision,
    StatementInput,
    TimestampTieCollision,
    UnrecognizedSection,
    detect_ordering_collisions,
    detect_orphaned_foreign_tax,
    detect_statement_input_conflicts,
    detect_symbol_currency_violations,
    detect_timestamp_tie_collisions,
    detect_unattributed_income,
    detect_unrecognized_sections,
    parse_acknowledged_gaps,
    partition_statements_by_metadata,
)

__all__ = [
    "EventStream",
    "ExtractionDefect",
    "TradeRow",
    "TransferRow",
    "StatementPeriod",
    "StatementMetadata",
    "parse_trades_stocklike",
    "parse_dividends",
    "parse_withholding_tax",
    "parse_syep_interest_details",
    "parse_interest",
    "parse_transfers",
    "parse_statement_metadata",
    "FifoMatcher",
    "RealizedLine",
    "Lot",
    "FxTable",
    "reconcile_realized_against_ibkr",
    "ReconciliationReport",
    "SymbolReconciliation",
    "ReportBuilder",
    "ExcelReportSink",
    "Quadro8ALine",
    "IbkrActivityStatementSource",
    "ParsedStatement",
    "OrderingCollision",
    "TimestampTieCollision",
    "StatementInput",
    "UnrecognizedSection",
    "detect_symbol_currency_violations",
    "detect_ordering_collisions",
    "detect_timestamp_tie_collisions",
    "detect_statement_input_conflicts",
    "detect_unattributed_income",
    "detect_orphaned_foreign_tax",
    "detect_unrecognized_sections",
    "parse_acknowledged_gaps",
    "partition_statements_by_metadata",
]
