from __future__ import annotations

from dataclasses import dataclass

from capitangains.model import IbkrModel

from .extract import (
    AssetScope,
    DividendRow,
    ExtractionDefect,
    InterestRow,
    SyepInterestRow,
    TradeRow,
    TransferRow,
    WithholdingRow,
    parse_dividends,
    parse_interest,
    parse_syep_interest_details,
    parse_trades_stocklike,
    parse_transfers,
    parse_withholding_tax,
)


@dataclass(frozen=True)
class ParsedStatement:
    """The six extracted section row-sets plus the union of their row-level defects.

    Read-side dual of what ReportBuilder is to the sink: one immutable typed snapshot of
    a read. Fields are tuples; ordering/year-filtering/FIFO sequencing are downstream
    concerns that copy these into fresh lists. Statement metadata is intentionally
    absent: it is read per file before the multi-file merge (the input-conflict gate),
    and a merged model holds several files' metadata rows, so a single period here would
    be ambiguous.
    """

    trades: tuple[TradeRow, ...]
    transfers: tuple[TransferRow, ...]
    dividends: tuple[DividendRow, ...]
    withholding: tuple[WithholdingRow, ...]
    syep_interest: tuple[SyepInterestRow, ...]
    interest: tuple[InterestRow, ...]
    defects: tuple[ExtractionDefect, ...]


@dataclass
class IbkrActivityStatementSource:
    """Read one merged IBKR activity-statement model into a ParsedStatement.

    Dual of ExcelReportSink: a configured object whose one method inverts the write.
    asset_scope is its single knob (mirroring ExcelReportSink.locale), forwarded to the
    trades extractor. Pure orchestration: no logging, no exit; the boundary keeps
    reporting.
    """

    asset_scope: AssetScope = "stocks_etfs"

    def read(self, model: IbkrModel) -> ParsedStatement:
        trades, trade_defects = parse_trades_stocklike(
            model, asset_scope=self.asset_scope
        )
        transfers, transfer_defects = parse_transfers(model)
        dividends, dividend_defects = parse_dividends(model)
        withholding, withholding_defects = parse_withholding_tax(model)
        syep_interest, syep_defects = parse_syep_interest_details(model)
        interest, interest_defects = parse_interest(model)
        return ParsedStatement(
            trades=tuple(trades),
            transfers=tuple(transfers),
            dividends=tuple(dividends),
            withholding=tuple(withholding),
            syep_interest=tuple(syep_interest),
            interest=tuple(interest),
            defects=(
                *trade_defects,
                *transfer_defects,
                *dividend_defects,
                *withholding_defects,
                *syep_defects,
                *interest_defects,
            ),
        )
