"""Per-cell characterization of the eight uniform table sheets in ExcelReportSink.

ExcelReportSink drives all eight table sheets through one data-driven engine plus a
declarative spec per sheet. Correctness rests on producing an identical workbook, and
the repo has no golden-file infrastructure, so the safety net is this explicit
(value, number_format) matrix: for every header and data cell of every table sheet,
under both locales, both the loaded value and the applied number format are pinned to
literals.

Cells whose expected format is "General" are the TEXT cells; pinning them locks the
load-bearing guarantee that the engine never assigns a number format to a text cell
(openpyxl's default "General" must survive untouched, a byte-preservation detail).

The summary sheet is intentionally excluded: it is a metric/value table whose labels
and values are covered by the test_summary_and_formats module (its money number formats
reuse the same NumberFormats.money exercised here). Its presence and position are still
locked via _SHEET_ORDER.
"""

import datetime as dt
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from capitangains.conv import Currency
from capitangains.reporting.extract import (
    DividendRow,
    InterestRow,
    SyepInterestRow,
    TransferRow,
    WithholdingRow,
)
from capitangains.reporting.i18n import NumberFormats, canonical_locale, labels_for
from capitangains.reporting.reconcile import ReconciliationReport, SymbolReconciliation
from capitangains.reporting.report_builder import ReportBuilder, RunMetadata
from capitangains.reporting.report_sink import ExcelReportSink
from tests.support import make_fx, realized_line


def _build_report() -> ReportBuilder:
    """Build one report populating all eight table sheets with the edge cases the
    sink must preserve.

    Exercised: every per-sheet sort key (in deliberately non-sorted insertion order); a
    multi-leg realized line (two anexo_j rows + a proceeds split) with one transferred
    and one synthetic leg; a gap line (gap_status + per_symbol has_gap) whose single leg
    has a None buy date (a nullable date in anexo_j); a line left unconverted because
    its currency has no FX rate (absent from realized + anexo_j, which source the
    converted lines, but still contributing its zero EUR aggregate to per_symbol); and
    nullable cash-flow EUR amounts (GBP dividend/withholding with no rate).
    """
    rb = ReportBuilder(year=2024)

    # Realized lines in non-sorted insertion order so realized/anexo_j (insertion order)
    # are distinguishable from per_symbol (sorted by symbol).
    mlt = realized_line(
        symbol="MLT",
        currency=Currency("USD"),
        sell_date=dt.date(2024, 6, 15),
        legs=[
            {
                "buy_date": dt.date(2023, 1, 1),
                "qty": Decimal("7"),
                "alloc_cost_ccy": Decimal("40"),
            },
            {
                "buy_date": dt.date(2023, 2, 1),
                "qty": Decimal("3"),
                "alloc_cost_ccy": Decimal("30"),
            },
        ],
    )
    mlt.legs[0].transferred = True
    mlt.legs[1].synthetic = True
    rb.add_realized(mlt)

    rb.add_realized(
        realized_line(
            symbol="GAP",
            currency=Currency("USD"),
            sell_date=dt.date(2024, 6, 20),
            legs=[
                {"buy_date": None, "qty": Decimal("5"), "alloc_cost_ccy": Decimal("0")}
            ],
            has_gap=True,
        )
    )
    rb.add_realized(
        realized_line(
            symbol="NOC",
            currency=Currency("GBP"),
            sell_date=dt.date(2024, 6, 30),
            legs=[
                {
                    "buy_date": dt.date(2023, 5, 1),
                    "qty": Decimal("2"),
                    "alloc_cost_ccy": Decimal("20"),
                }
            ],
        )
    )

    rb.set_dividends(
        [
            DividendRow(Currency("USD"), dt.date(2024, 2, 1), "Zulu", Decimal("5")),
            DividendRow(Currency("USD"), dt.date(2024, 2, 2), "alpha", Decimal("3")),
            DividendRow(Currency("GBP"), dt.date(2024, 2, 3), "Beta", Decimal("7")),
        ]
    )
    rb.set_interest(
        [
            InterestRow(Currency("USD"), dt.date(2024, 3, 1), "Zeta", Decimal("2")),
            InterestRow(Currency("USD"), dt.date(2024, 3, 2), "apex", Decimal("1")),
        ]
    )
    rb.set_withholding(
        [
            WithholdingRow(
                Currency("EUR"),
                dt.date(2024, 4, 2),
                "Zulu",
                Decimal("-1"),
                "",
                "Interest",
                "",
            ),
            WithholdingRow(
                Currency("EUR"),
                dt.date(2024, 4, 3),
                "Alpha",
                Decimal("-1.5"),
                "",
                "Dividend",
                "",
            ),
            WithholdingRow(
                Currency("USD"),
                dt.date(2024, 4, 1),
                "Bravo",
                Decimal("-2"),
                "",
                "Dividend",
                "US",
            ),
            WithholdingRow(
                Currency("GBP"),
                dt.date(2024, 4, 4),
                "Gamma",
                Decimal("-3"),
                "",
                "Unknown",
                "GB",
            ),
        ]
    )
    rb.set_syep_interest(
        [
            SyepInterestRow(
                currency=Currency("USD"),
                value_date=dt.date(2024, 5, 1),
                symbol="LEND",
                start_date=dt.date(2023, 12, 1),
                quantity=Decimal("-10"),
                collateral_amount=Decimal("1000"),
                market_rate_pct=Decimal("5.25"),
                customer_rate_pct=Decimal("2.5"),
                interest_paid=Decimal("3"),
                code="",
            ),
        ]
    )
    rb.set_transfers(
        [
            TransferRow(
                section="Transfers",
                asset_category="Stocks",
                currency=Currency("USD"),
                symbol="ZZZ",
                date=dt.date(2024, 3, 1),
                direction="Out",
                quantity=Decimal("5"),
                market_value=Decimal("0"),
                code="",
            ),
            TransferRow(
                section="Transfers",
                asset_category="Stocks",
                currency=Currency("USD"),
                symbol="AAA",
                date=dt.date(2024, 1, 1),
                direction="In",
                quantity=Decimal("10"),
                market_value=Decimal("1000"),
                code="FX",
            ),
            TransferRow(
                section="Transfers",
                asset_category="Stocks",
                currency=Currency("EUR"),
                symbol="BBB",
                date=dt.date(2024, 1, 1),
                direction="In",
                quantity=Decimal("20"),
                market_value=Decimal("2000"),
                code="",
            ),
        ]
    )

    # USD rates for every USD date in play; GBP deliberately absent (leaves NOC and the
    # GBP cash-flow rows unconverted); EUR is identity inside the converter.
    rb.convert_eur(
        make_fx(
            {
                ("USD", "2023-01-01"): Decimal("0.9"),
                ("USD", "2023-02-01"): Decimal("0.9"),
                ("USD", "2024-02-01"): Decimal("0.9"),
                ("USD", "2024-02-02"): Decimal("0.9"),
                ("USD", "2024-03-01"): Decimal("0.9"),
                ("USD", "2024-03-02"): Decimal("0.9"),
                ("USD", "2024-04-01"): Decimal("0.9"),
                ("USD", "2024-05-01"): Decimal("0.9"),
                ("USD", "2024-06-15"): Decimal("0.9"),
                ("USD", "2024-06-20"): Decimal("0.9"),
            }
        )
    )

    # Instrument countries for the capital-gains source column: MLT maps to US (which
    # renders as its Tabela X code 840), while GAP is deliberately absent so its 9.2A
    # source country renders blank.
    rb.instrument_country = {"MLT": "US"}

    # A representative reconciliation exercising every Status the sheet renders:
    # a clean match, a magnitude mismatch, a sign flip, a synthesized (unconfirmed) key,
    # an incomplete (expected-Forex) skip, and an anomalous non-Forex blank.
    usd, gbp = Currency("USD"), Currency("GBP")
    rb.reconciliation = ReconciliationReport(
        reconciled=[
            SymbolReconciliation("AOK", usd, Decimal("30.00"), Decimal("30.00"), 1),
            SymbolReconciliation("BAD", gbp, Decimal("80.00"), Decimal("50.00"), 1),
            SymbolReconciliation("FLIP", usd, Decimal("100.00"), Decimal("-20.00"), 1),
        ],
        synthetic=[
            SymbolReconciliation("SYN", usd, Decimal("15.00"), Decimal("15.00"), 1),
        ],
        incomplete=[("FXROW", usd)],
        anomalous_elision=[("ANOM", usd)],
    )
    # Fixed provenance so the Cover renders deterministically (its cells are asserted in
    # a dedicated test; here it only needs to exist and lead the workbook).
    rb.metadata = RunMetadata(
        account="U1234567",
        period_start=dt.date(2024, 1, 1),
        period_end=dt.date(2024, 12, 31),
        tax_year=2024,
        fx_source="fx.csv",
        fx_currencies=frozenset({usd, gbp}),
        fx_date_range=(dt.date(2023, 1, 1), dt.date(2024, 12, 31)),
        tool_version="0.1.0",
        generated_at=dt.datetime(2026, 7, 2, 14, 30, 0),
    )
    return rb


# Expected sheet titles per locale; see module docstring. Non-ASCII label and
# currency-symbol text is escaped so the matrix stays byte-exact and ASCII-only.
_SHEET_ORDER: dict[str, list[str]] = {
    "EN": [
        "Cover",
        "Trading Totals",
        "Annex J - Box 9.2A",
        "Annex J - Box 8A",
        "IBKR Reconciliation",
        "Realized Trades",
        "Per Symbol Summary",
        "Dividends",
        "Account Interest",
        "SYEP Interest",
        "Withholding Tax",
        "Stock Transfers",
    ],
    "PT": [
        "Capa",
        "Totais de Opera\xe7\xf5es",
        "Anexo J - Quadro 9.2A",
        "Anexo J - Quadro 8A",
        "Reconcilia\xe7\xe3o IBKR",
        "Opera\xe7\xf5es Realizadas",
        "Resumo por S\xedmbolo",
        "Dividendos",
        "Juros da Conta",
        "Juros SYEP",
        "Reten\xe7\xe3o na Fonte",
        "Transfer\xeancias de A\xe7\xf5es",
    ],
}

_EXPECTED: dict[str, dict[str, list]] = {
    "EN": {
        "Annex J - Box 9.2A": [
            [
                ("Source Country", "General"),
                ("Code", "General"),
                ("Disp. Year", "General"),
                ("Disp. Month", "General"),
                ("Disp. Day", "General"),
                ("Realization Value (EUR)", "General"),
                ("Acq. Year", "General"),
                ("Acq. Month", "General"),
                ("Acq. Day", "General"),
                ("Acquisition Value (EUR)", "General"),
                ("Expenses (EUR)", "General"),
                ("Foreign Tax (EUR)", "General"),
                ("Counterparty Country", "General"),
                ("Admitted to Trading?", "General"),
                ("Ticker", "General"),
                ("Trade Currency", "General"),
                ("Quantity", "General"),
                ("FX Rate (Acq.)", "General"),
                ("Acquisition Date", "General"),
                ("Disposal Date", "General"),
                ("Realized P/L (EUR)", "General"),
                ("Transferred", "General"),
                ("Estimated Basis", "General"),
            ],
            [
                ("840", "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (15, "General"),
                (63, "\u20ac#,##0.00"),
                (2023, "General"),
                (1, "General"),
                (1, "General"),
                (36, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("MLT", "General"),
                ("USD", "General"),
                (7, "0.########"),
                (0.9, "0.########"),
                (dt.datetime(2023, 1, 1, 0, 0), "YYYY-MM-DD"),
                (dt.datetime(2024, 6, 15, 0, 0), "YYYY-MM-DD"),
                (27, "\u20ac#,##0.00"),
                ("Yes", "General"),
                (None, "General"),
            ],
            [
                ("840", "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (15, "General"),
                (27, "\u20ac#,##0.00"),
                (2023, "General"),
                (2, "General"),
                (1, "General"),
                (27, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("MLT", "General"),
                ("USD", "General"),
                (3, "0.########"),
                (0.9, "0.########"),
                (dt.datetime(2023, 2, 1, 0, 0), "YYYY-MM-DD"),
                (dt.datetime(2024, 6, 15, 0, 0), "YYYY-MM-DD"),
                (0, "\u20ac#,##0.00"),
                (None, "General"),
                ("Yes", "General"),
            ],
            [
                (None, "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (20, "General"),
                (90, "\u20ac#,##0.00"),
                (None, "General"),
                (None, "General"),
                (None, "General"),
                (0, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("GAP", "General"),
                ("USD", "General"),
                (5, "0.########"),
                (0.9, "0.########"),
                (None, "YYYY-MM-DD"),
                (dt.datetime(2024, 6, 20, 0, 0), "YYYY-MM-DD"),
                (90, "\u20ac#,##0.00"),
                (None, "General"),
                (None, "General"),
            ],
        ],
        "Annex J - Box 8A": [
            [
                ("Income Code", "General"),
                ("Source Country", "General"),
                ("Gross Income (EUR)", "General"),
                ("Foreign Tax (EUR)", "General"),
                ("Type", "General"),
            ],
            [
                ("E11", "General"),
                (None, "General"),
                (7.2, "\u20ac#,##0.00"),
                (1.5, "\u20ac#,##0.00"),
                ("Dividend", "General"),
            ],
            [
                ("E11", "General"),
                ("840", "General"),
                (0, "\u20ac#,##0.00"),
                (1.8, "\u20ac#,##0.00"),
                ("Dividend", "General"),
            ],
            [
                ("E21", "General"),
                ("372", "General"),
                (2.7, "\u20ac#,##0.00"),
                (1, "\u20ac#,##0.00"),
                ("Interest", "General"),
            ],
        ],
        "IBKR Reconciliation": [
            [
                ("Symbol", "General"),
                ("Currency", "General"),
                ("This Report", "General"),
                ("IBKR", "General"),
                ("Difference", "General"),
                ("Status", "General"),
            ],
            [
                ("AOK", "General"),
                ("USD", "General"),
                (30, "$#,##0.00"),
                (30, "$#,##0.00"),
                (0, "$#,##0.00"),
                ("OK", "General"),
            ],
            [
                ("BAD", "General"),
                ("GBP", "General"),
                (80, "\xa3#,##0.00"),
                (50, "\xa3#,##0.00"),
                (30, "\xa3#,##0.00"),
                ("Mismatch", "General"),
            ],
            [
                ("FLIP", "General"),
                ("USD", "General"),
                (100, "$#,##0.00"),
                (-20, "$#,##0.00"),
                (120, "$#,##0.00"),
                ("Sign Flip", "General"),
            ],
            [
                ("SYN", "General"),
                ("USD", "General"),
                (15, "$#,##0.00"),
                (15, "$#,##0.00"),
                (0, "$#,##0.00"),
                ("Synthesized, unconfirmed", "General"),
            ],
            [
                ("FXROW", "General"),
                ("USD", "General"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                ("Not cross-checked (Forex)", "General"),
            ],
            [
                ("ANOM", "General"),
                ("USD", "General"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                ("Anomalous, no IBKR P/L", "General"),
            ],
        ],
        "Realized Trades": [
            [
                ("Ticker", "General"),
                ("Trade Currency", "General"),
                ("Sell Date", "General"),
                ("Quantity Sold", "General"),
                ("Gross Proceeds", "General"),
                ("Commissions/Fees", "General"),
                ("Net Proceeds", "General"),
                ("Allocated Cost Basis", "General"),
                ("Realized P/L", "General"),
                ("Gross Proceeds (EUR)", "General"),
                ("Commissions/Fees (EUR)", "General"),
                ("Net Proceeds (EUR)", "General"),
                ("Allocated Cost Basis (EUR)", "General"),
                ("Realized P/L (EUR)", "General"),
                ("FX Rate", "General"),
                ("Basis Status", "General"),
            ],
            [
                ("MLT", "General"),
                ("USD", "General"),
                (dt.datetime(2024, 6, 15, 0, 0), "YYYY-MM-DD"),
                (10, "0.########"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (70, "$#,##0.00"),
                (30, "$#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (63, "\u20ac#,##0.00"),
                (27, "\u20ac#,##0.00"),
                (0.9, "0.########"),
                (None, "General"),
            ],
            [
                ("GAP", "General"),
                ("USD", "General"),
                (dt.datetime(2024, 6, 20, 0, 0), "YYYY-MM-DD"),
                (5, "0.########"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (0.9, "0.########"),
                ("Cost basis missing, treated as zero", "General"),
            ],
        ],
        "Per Symbol Summary": [
            [
                ("Ticker", "General"),
                ("Trade Currency", "General"),
                ("Realized P/L", "General"),
                ("Net Proceeds", "General"),
                ("Allocated Cost Basis", "General"),
                ("Realized P/L (EUR)", "General"),
                ("Net Proceeds (EUR)", "General"),
                ("Allocated Cost Basis (EUR)", "General"),
                ("Estimated Basis", "General"),
            ],
            [
                ("GAP", "General"),
                ("USD", "General"),
                (100, "$#,##0.00"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                ("Yes", "General"),
            ],
            [
                ("MLT", "General"),
                ("USD", "General"),
                (30, "$#,##0.00"),
                (100, "$#,##0.00"),
                (70, "$#,##0.00"),
                (27, "\u20ac#,##0.00"),
                (90, "\u20ac#,##0.00"),
                (63, "\u20ac#,##0.00"),
                (None, "General"),
            ],
            [
                ("NOC", "General"),
                ("GBP", "General"),
                (80, "\xa3#,##0.00"),
                (100, "\xa3#,##0.00"),
                (20, "\xa3#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (0, "\u20ac#,##0.00"),
                (None, "General"),
            ],
        ],
        "Dividends": [
            [
                ("Date", "General"),
                ("Currency", "General"),
                ("Description", "General"),
                ("Amount (Currency)", "General"),
                ("Amount (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 2, 2, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("alpha", "General"),
                (3, "$#,##0.00"),
                (2.7, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 2, 3, 0, 0), "YYYY-MM-DD"),
                ("GBP", "General"),
                ("Beta", "General"),
                (7, "\xa3#,##0.00"),
                (None, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 2, 1, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("Zulu", "General"),
                (5, "$#,##0.00"),
                (4.5, "\u20ac#,##0.00"),
            ],
        ],
        "Account Interest": [
            [
                ("Date", "General"),
                ("Currency", "General"),
                ("Description", "General"),
                ("Amount (Currency)", "General"),
                ("Amount (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 3, 2, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("apex", "General"),
                (1, "$#,##0.00"),
                (0.9, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 3, 1, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("Zeta", "General"),
                (2, "$#,##0.00"),
                (1.8, "\u20ac#,##0.00"),
            ],
        ],
        "SYEP Interest": [
            [
                ("Value Date", "General"),
                ("Currency", "General"),
                ("Symbol", "General"),
                ("Start Date", "General"),
                ("Quantity", "General"),
                ("Collateral Amount", "General"),
                ("Market Rate (%)", "General"),
                ("Customer Rate (%)", "General"),
                ("Interest Paid (Currency)", "General"),
                ("Interest Paid (EUR)", "General"),
                ("Code", "General"),
            ],
            [
                (dt.datetime(2024, 5, 1, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("LEND", "General"),
                (dt.datetime(2023, 12, 1, 0, 0), "YYYY-MM-DD"),
                (-10, "0.########"),
                (1000, "$#,##0.00"),
                (5.25, "0.00####"),
                (2.5, "0.00####"),
                (3, "$#,##0.00"),
                (2.7, "\u20ac#,##0.00"),
                (None, "General"),
            ],
        ],
        "Withholding Tax": [
            [
                ("Date", "General"),
                ("Currency", "General"),
                ("Description", "General"),
                ("Type", "General"),
                ("Country", "General"),
                ("Amount (Currency)", "General"),
                ("Amount (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 4, 3, 0, 0), "YYYY-MM-DD"),
                ("EUR", "General"),
                ("Alpha", "General"),
                ("Dividend", "General"),
                (None, "General"),
                (-1.5, "\u20ac#,##0.00"),
                (-1.5, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 4, 2, 0, 0), "YYYY-MM-DD"),
                ("EUR", "General"),
                ("Zulu", "General"),
                ("Interest", "General"),
                (None, "General"),
                (-1, "\u20ac#,##0.00"),
                (-1, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 4, 4, 0, 0), "YYYY-MM-DD"),
                ("GBP", "General"),
                ("Gamma", "General"),
                ("Unknown", "General"),
                ("GB", "General"),
                (-3, "\xa3#,##0.00"),
                (None, "\u20ac#,##0.00"),
            ],
            [
                (dt.datetime(2024, 4, 1, 0, 0), "YYYY-MM-DD"),
                ("USD", "General"),
                ("Bravo", "General"),
                ("Dividend", "General"),
                ("US", "General"),
                (-2, "$#,##0.00"),
                (-1.8, "\u20ac#,##0.00"),
            ],
        ],
        "Stock Transfers": [
            [
                ("Date", "General"),
                ("Symbol", "General"),
                ("Direction", "General"),
                ("Quantity", "General"),
                ("Currency", "General"),
                ("Market Value", "General"),
                ("Code", "General"),
            ],
            [
                (dt.datetime(2024, 1, 1, 0, 0), "YYYY-MM-DD"),
                ("AAA", "General"),
                ("In", "General"),
                (10, "0.########"),
                ("USD", "General"),
                (1000, "$#,##0.00"),
                ("FX", "General"),
            ],
            [
                (dt.datetime(2024, 1, 1, 0, 0), "YYYY-MM-DD"),
                ("BBB", "General"),
                ("In", "General"),
                (20, "0.########"),
                ("EUR", "General"),
                (2000, "\u20ac#,##0.00"),
                (None, "General"),
            ],
            [
                (dt.datetime(2024, 3, 1, 0, 0), "YYYY-MM-DD"),
                ("ZZZ", "General"),
                ("Out", "General"),
                (5, "0.########"),
                ("USD", "General"),
                (0, "$#,##0.00"),
                (None, "General"),
            ],
        ],
    },
    "PT": {
        "Anexo J - Quadro 9.2A": [
            [
                ("Pa\xeds da Fonte", "General"),
                ("C\xf3digo", "General"),
                ("Ano Realiza\xe7\xe3o", "General"),
                ("M\xeas Realiza\xe7\xe3o", "General"),
                ("Dia Realiza\xe7\xe3o", "General"),
                ("Valor de Realiza\xe7\xe3o (EUR)", "General"),
                ("Ano Aquisi\xe7\xe3o", "General"),
                ("M\xeas Aquisi\xe7\xe3o", "General"),
                ("Dia Aquisi\xe7\xe3o", "General"),
                ("Valor de Aquisi\xe7\xe3o (EUR)", "General"),
                ("Despesas (EUR)", "General"),
                ("Imposto Pago no Estrangeiro (EUR)", "General"),
                ("Pa\xeds da Contraparte", "General"),
                ("Admitido \xe0 Negocia\xe7\xe3o?", "General"),
                ("S\xedmbolo", "General"),
                ("Moeda da Opera\xe7\xe3o", "General"),
                ("Quantidade", "General"),
                ("C\xe2mbio (Aquisi\xe7\xe3o)", "General"),
                ("Data de Aquisi\xe7\xe3o", "General"),
                ("Data de Venda", "General"),
                ("Mais/menos-valia (EUR)", "General"),
                ("Transferido", "General"),
                ("Custo Estimado", "General"),
            ],
            [
                ("840", "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (15, "General"),
                (63, '#,##0.00 "\u20ac"'),
                (2023, "General"),
                (1, "General"),
                (1, "General"),
                (36, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("MLT", "General"),
                ("USD", "General"),
                (7, "0.########"),
                (0.9, "0.########"),
                (dt.datetime(2023, 1, 1, 0, 0), "DD/MM/YYYY"),
                (dt.datetime(2024, 6, 15, 0, 0), "DD/MM/YYYY"),
                (27, '#,##0.00 "\u20ac"'),
                ("Yes", "General"),
                (None, "General"),
            ],
            [
                ("840", "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (15, "General"),
                (27, '#,##0.00 "\u20ac"'),
                (2023, "General"),
                (2, "General"),
                (1, "General"),
                (27, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("MLT", "General"),
                ("USD", "General"),
                (3, "0.########"),
                (0.9, "0.########"),
                (dt.datetime(2023, 2, 1, 0, 0), "DD/MM/YYYY"),
                (dt.datetime(2024, 6, 15, 0, 0), "DD/MM/YYYY"),
                (0, '#,##0.00 "\u20ac"'),
                (None, "General"),
                ("Yes", "General"),
            ],
            [
                (None, "General"),
                ("G01", "General"),
                (2024, "General"),
                (6, "General"),
                (20, "General"),
                (90, '#,##0.00 "\u20ac"'),
                (None, "General"),
                (None, "General"),
                (None, "General"),
                (0, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (None, "General"),
                ("372", "General"),
                (None, "General"),
                ("GAP", "General"),
                ("USD", "General"),
                (5, "0.########"),
                (0.9, "0.########"),
                (None, "DD/MM/YYYY"),
                (dt.datetime(2024, 6, 20, 0, 0), "DD/MM/YYYY"),
                (90, '#,##0.00 "\u20ac"'),
                (None, "General"),
                (None, "General"),
            ],
        ],
        "Anexo J - Quadro 8A": [
            [
                ("C\xf3digo Rendimento", "General"),
                ("Pa\xeds da Fonte", "General"),
                ("Rendimento Bruto (EUR)", "General"),
                ("Imposto Pago no Estrangeiro (EUR)", "General"),
                ("Tipo", "General"),
            ],
            [
                ("E11", "General"),
                (None, "General"),
                (7.2, '#,##0.00 "\u20ac"'),
                (1.5, '#,##0.00 "\u20ac"'),
                ("Dividendo", "General"),
            ],
            [
                ("E11", "General"),
                ("840", "General"),
                (0, '#,##0.00 "\u20ac"'),
                (1.8, '#,##0.00 "\u20ac"'),
                ("Dividendo", "General"),
            ],
            [
                ("E21", "General"),
                ("372", "General"),
                (2.7, '#,##0.00 "\u20ac"'),
                (1, '#,##0.00 "\u20ac"'),
                ("Juros", "General"),
            ],
        ],
        "Reconcilia\xe7\xe3o IBKR": [
            [
                ("S\xedmbolo", "General"),
                ("Moeda", "General"),
                ("Este Relat\xf3rio", "General"),
                ("IBKR", "General"),
                ("Diferen\xe7a", "General"),
                ("Estado", "General"),
            ],
            [
                ("AOK", "General"),
                ("USD", "General"),
                (30, "$#,##0.00"),
                (30, "$#,##0.00"),
                (0, "$#,##0.00"),
                ("OK", "General"),
            ],
            [
                ("BAD", "General"),
                ("GBP", "General"),
                (80, "\xa3#,##0.00"),
                (50, "\xa3#,##0.00"),
                (30, "\xa3#,##0.00"),
                ("Diverg\xeancia", "General"),
            ],
            [
                ("FLIP", "General"),
                ("USD", "General"),
                (100, "$#,##0.00"),
                (-20, "$#,##0.00"),
                (120, "$#,##0.00"),
                ("Sinal Invertido", "General"),
            ],
            [
                ("SYN", "General"),
                ("USD", "General"),
                (15, "$#,##0.00"),
                (15, "$#,##0.00"),
                (0, "$#,##0.00"),
                ("Sintetizado, n\xe3o confirmado", "General"),
            ],
            [
                ("FXROW", "General"),
                ("USD", "General"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                ("N\xe3o verificado (Forex)", "General"),
            ],
            [
                ("ANOM", "General"),
                ("USD", "General"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                (None, "$#,##0.00"),
                ("An\xf3malo, sem P/L IBKR", "General"),
            ],
        ],
        "Opera\xe7\xf5es Realizadas": [
            [
                ("S\xedmbolo", "General"),
                ("Moeda da Opera\xe7\xe3o", "General"),
                ("Data de Venda", "General"),
                ("Quantidade Vendida", "General"),
                ("Proveitos Brutos", "General"),
                ("Comiss\xf5es/Taxas", "General"),
                ("Proveitos L\xedquidos", "General"),
                ("Custo Alocado", "General"),
                ("Resultado Realizado", "General"),
                ("Proveitos Brutos (EUR)", "General"),
                ("Comiss\xf5es/Taxas (EUR)", "General"),
                ("Proveitos L\xedquidos (EUR)", "General"),
                ("Custo Alocado (EUR)", "General"),
                ("Resultado Realizado (EUR)", "General"),
                ("C\xe2mbio", "General"),
                ("Estado do Custo", "General"),
            ],
            [
                ("MLT", "General"),
                ("USD", "General"),
                (dt.datetime(2024, 6, 15, 0, 0), "DD/MM/YYYY"),
                (10, "0.########"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (70, "$#,##0.00"),
                (30, "$#,##0.00"),
                (90, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (90, '#,##0.00 "\u20ac"'),
                (63, '#,##0.00 "\u20ac"'),
                (27, '#,##0.00 "\u20ac"'),
                (0.9, "0.########"),
                (None, "General"),
            ],
            [
                ("GAP", "General"),
                ("USD", "General"),
                (dt.datetime(2024, 6, 20, 0, 0), "DD/MM/YYYY"),
                (5, "0.########"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (100, "$#,##0.00"),
                (90, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (90, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (90, '#,##0.00 "\u20ac"'),
                (0.9, "0.########"),
                ("Custo em falta, considerado zero", "General"),
            ],
        ],
        "Resumo por S\xedmbolo": [
            [
                ("S\xedmbolo", "General"),
                ("Moeda da Opera\xe7\xe3o", "General"),
                ("Resultado Realizado", "General"),
                ("Proveitos L\xedquidos", "General"),
                ("Custo Alocado", "General"),
                ("Resultado Realizado (EUR)", "General"),
                ("Proveitos L\xedquidos (EUR)", "General"),
                ("Custo Alocado (EUR)", "General"),
                ("Custo Estimado", "General"),
            ],
            [
                ("GAP", "General"),
                ("USD", "General"),
                (100, "$#,##0.00"),
                (100, "$#,##0.00"),
                (0, "$#,##0.00"),
                (90, '#,##0.00 "\u20ac"'),
                (90, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                ("Yes", "General"),
            ],
            [
                ("MLT", "General"),
                ("USD", "General"),
                (30, "$#,##0.00"),
                (100, "$#,##0.00"),
                (70, "$#,##0.00"),
                (27, '#,##0.00 "\u20ac"'),
                (90, '#,##0.00 "\u20ac"'),
                (63, '#,##0.00 "\u20ac"'),
                (None, "General"),
            ],
            [
                ("NOC", "General"),
                ("GBP", "General"),
                (80, "\xa3#,##0.00"),
                (100, "\xa3#,##0.00"),
                (20, "\xa3#,##0.00"),
                (0, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (0, '#,##0.00 "\u20ac"'),
                (None, "General"),
            ],
        ],
        "Dividendos": [
            [
                ("Data", "General"),
                ("Moeda", "General"),
                ("Descri\xe7\xe3o", "General"),
                ("Montante (Moeda)", "General"),
                ("Montante (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 2, 2, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("alpha", "General"),
                (3, "$#,##0.00"),
                (2.7, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 2, 3, 0, 0), "DD/MM/YYYY"),
                ("GBP", "General"),
                ("Beta", "General"),
                (7, "\xa3#,##0.00"),
                (None, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 2, 1, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("Zulu", "General"),
                (5, "$#,##0.00"),
                (4.5, '#,##0.00 "\u20ac"'),
            ],
        ],
        "Juros da Conta": [
            [
                ("Data", "General"),
                ("Moeda", "General"),
                ("Descri\xe7\xe3o", "General"),
                ("Montante (Moeda)", "General"),
                ("Montante (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 3, 2, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("apex", "General"),
                (1, "$#,##0.00"),
                (0.9, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 3, 1, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("Zeta", "General"),
                (2, "$#,##0.00"),
                (1.8, '#,##0.00 "\u20ac"'),
            ],
        ],
        "Juros SYEP": [
            [
                ("Data", "General"),
                ("Moeda", "General"),
                ("S\xedmbolo", "General"),
                ("Data de In\xedcio", "General"),
                ("Quantidade", "General"),
                ("Valor de Colateral", "General"),
                ("Taxa de Mercado (%)", "General"),
                ("Taxa ao Cliente (%)", "General"),
                ("Juros Pagos (Moeda)", "General"),
                ("Juros Pagos (EUR)", "General"),
                ("C\xf3digo", "General"),
            ],
            [
                (dt.datetime(2024, 5, 1, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("LEND", "General"),
                (dt.datetime(2023, 12, 1, 0, 0), "DD/MM/YYYY"),
                (-10, "0.########"),
                (1000, "$#,##0.00"),
                (5.25, "0.00####"),
                (2.5, "0.00####"),
                (3, "$#,##0.00"),
                (2.7, '#,##0.00 "\u20ac"'),
                (None, "General"),
            ],
        ],
        "Reten\xe7\xe3o na Fonte": [
            [
                ("Data", "General"),
                ("Moeda", "General"),
                ("Descri\xe7\xe3o", "General"),
                ("Tipo", "General"),
                ("Pa\xeds", "General"),
                ("Montante (Moeda)", "General"),
                ("Montante (EUR)", "General"),
            ],
            [
                (dt.datetime(2024, 4, 3, 0, 0), "DD/MM/YYYY"),
                ("EUR", "General"),
                ("Alpha", "General"),
                ("Dividend", "General"),
                (None, "General"),
                (-1.5, '#,##0.00 "\u20ac"'),
                (-1.5, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 4, 2, 0, 0), "DD/MM/YYYY"),
                ("EUR", "General"),
                ("Zulu", "General"),
                ("Interest", "General"),
                (None, "General"),
                (-1, '#,##0.00 "\u20ac"'),
                (-1, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 4, 4, 0, 0), "DD/MM/YYYY"),
                ("GBP", "General"),
                ("Gamma", "General"),
                ("Unknown", "General"),
                ("GB", "General"),
                (-3, "\xa3#,##0.00"),
                (None, '#,##0.00 "\u20ac"'),
            ],
            [
                (dt.datetime(2024, 4, 1, 0, 0), "DD/MM/YYYY"),
                ("USD", "General"),
                ("Bravo", "General"),
                ("Dividend", "General"),
                ("US", "General"),
                (-2, "$#,##0.00"),
                (-1.8, '#,##0.00 "\u20ac"'),
            ],
        ],
        "Transfer\xeancias de A\xe7\xf5es": [
            [
                ("Data", "General"),
                ("S\xedmbolo", "General"),
                ("Dire\xe7\xe3o", "General"),
                ("Quantidade", "General"),
                ("Moeda", "General"),
                ("Valor de Mercado", "General"),
                ("C\xf3digo", "General"),
            ],
            [
                (dt.datetime(2024, 1, 1, 0, 0), "DD/MM/YYYY"),
                ("AAA", "General"),
                ("In", "General"),
                (10, "0.########"),
                ("USD", "General"),
                (1000, "$#,##0.00"),
                ("FX", "General"),
            ],
            [
                (dt.datetime(2024, 1, 1, 0, 0), "DD/MM/YYYY"),
                ("BBB", "General"),
                ("In", "General"),
                (20, "0.########"),
                ("EUR", "General"),
                (2000, '#,##0.00 "\u20ac"'),
                (None, "General"),
            ],
            [
                (dt.datetime(2024, 3, 1, 0, 0), "DD/MM/YYYY"),
                ("ZZZ", "General"),
                ("Out", "General"),
                (5, "0.########"),
                ("USD", "General"),
                (0, "$#,##0.00"),
                (None, "General"),
            ],
        ],
    },
}


def _grid(ws) -> list:
    return [
        [(cell.value, cell.number_format) for cell in row] for row in ws.iter_rows()
    ]


@pytest.mark.parametrize("locale", ["EN", "PT"])
def test_table_sheets_characterization(tmp_path, locale):
    rb = _build_report()
    out = tmp_path / f"report_{locale}.xlsx"
    ExcelReportSink(out_path=out, locale=locale).write(rb)
    wb = load_workbook(out)

    assert wb.sheetnames == _SHEET_ORDER[locale]

    for title, exp_rows in _EXPECTED[locale].items():
        actual = _grid(wb[title])
        assert len(actual) == len(exp_rows), f"[{locale}] {title}: row count"
        for r, (exp_row, act_row) in enumerate(
            zip(exp_rows, actual, strict=True), start=1
        ):
            assert act_row == exp_row, f"[{locale}] {title} row {r}"


def test_optional_sheets_omitted_when_source_empty(tmp_path):
    """skip_if_empty: with every optional collection empty (and no reconciliation or
    metadata), only the four always-on sheets are written (summary, 9.2A, realized,
    per_symbol), in the leading-fileable order. The optional sheets (quadro_8a,
    reconciliation, dividends, interest, syep, withholding, transfers) and the
    metadata-only Cover are absent.
    """
    rb = ReportBuilder(year=2024)
    rb.add_realized(
        realized_line(
            symbol="ONLY",
            currency=Currency("USD"),
            sell_date=dt.date(2024, 6, 1),
            legs=[
                {
                    "buy_date": dt.date(2023, 1, 1),
                    "qty": Decimal("1"),
                    "alloc_cost_ccy": Decimal("10"),
                }
            ],
        )
    )
    out = tmp_path / "sparse.xlsx"
    ExcelReportSink(out_path=out, locale="EN").write(rb)
    assert load_workbook(out).sheetnames == [
        "Trading Totals",
        "Annex J - Box 9.2A",
        "Realized Trades",
        "Per Symbol Summary",
    ]


@pytest.mark.parametrize(
    "locale,ccy,expected",
    [
        ("EN", "JPY", "\u00a5#,##0.00"),  # symbol-mapped, non-EUR: prefixed symbol
        ("PT", "JPY", "\u00a5#,##0.00"),  # JPY is not special-cased like EUR/PT
        ("EN", "CHF", '"CHF" #,##0.00'),  # unmapped currency: quoted ISO, EN order
        ("PT", "CHF", '#,##0.00 "CHF"'),  # unmapped currency: quoted ISO, PT order
    ],
)
def test_money_format_fallbacks(locale, ccy, expected):
    assert NumberFormats(locale).money(ccy) == expected


def test_empty_locale_resolves_to_pt_for_labels_and_formats():
    # An empty/unset locale resolves (once, at the boundary) to PT, the report's
    # default, driving the same language for labels and number/date formats: never
    # PT labels with the EN date format.
    loc = canonical_locale("")
    assert loc == "PT"
    assert labels_for(loc)["sheet"]["realized"] == "Operações Realizadas"  # PT label
    assert NumberFormats(loc).date == "DD/MM/YYYY"  # PT format, the same language


def test_unsupported_locale_is_rejected_not_coerced(tmp_path):
    # A non-empty unsupported locale is a caller error: the sole parse point fails
    # loudly rather than silently substitute PT (the guess the codebase refuses).
    with pytest.raises(ValueError, match="Unsupported locale"):
        canonical_locale("DE")
    # And it is caught at the sink boundary, before any workbook is written.
    with pytest.raises(ValueError, match="Unsupported locale"):
        ExcelReportSink(out_path=tmp_path / "report.xlsx", locale="DE")
