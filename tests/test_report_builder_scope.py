"""ReportBuilder is scoped to its ``year``: every bulk ingest method retains only the
rows dated in that year, so the full multi-file parse (kept whole upstream to seed FIFO)
can be handed over verbatim and the builder does the scoping the sink depends on. SYEP
rows without a value date cannot be placed in any year and are dropped (the rows the
extractor leaves undated for CSV 'Total' lines).
"""

import datetime as dt
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import DividendRow, SyepInterestRow
from capitangains.reporting.report_builder import ReportBuilder
from tests.support import realized_line


def _syep(symbol, value_date):
    return SyepInterestRow(
        currency=Currency("USD"),
        value_date=value_date,
        symbol=symbol,
        start_date=None,
        quantity=Decimal("-10"),
        collateral_amount=Decimal("1000"),
        market_rate_pct=Decimal("5"),
        customer_rate_pct=Decimal("2"),
        interest_paid=Decimal("3"),
        code="",
    )


def test_add_realized_lines_keeps_only_sells_in_report_year():
    rb = ReportBuilder(year=2024)
    rb.add_realized_lines(
        [
            realized_line(sell_date=dt.date(2023, 12, 31)),
            realized_line(sell_date=dt.date(2024, 3, 1)),
            realized_line(sell_date=dt.date(2025, 1, 1)),
        ]
    )
    assert [rl.sell_date for rl in rb.realized_lines] == [dt.date(2024, 3, 1)]


def test_set_dividends_drops_rows_outside_report_year():
    rb = ReportBuilder(year=2024)
    rb.set_dividends(
        [
            DividendRow(Currency("USD"), dt.date(2023, 6, 1), "prior", Decimal("10")),
            DividendRow(Currency("USD"), dt.date(2024, 6, 1), "current", Decimal("20")),
        ]
    )
    assert [d.description for d in rb.dividends] == ["current"]


def test_set_syep_interest_drops_rows_without_a_value_date():
    rb = ReportBuilder(year=2024)
    rb.set_syep_interest([_syep("LEND", dt.date(2024, 5, 1)), _syep("NODT", None)])
    assert [r.symbol for r in rb.syep_interest] == ["LEND"]
