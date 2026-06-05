from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .fifo_domain import RealizedLine
from .money import quantize_money
from .report_builder import ReportBuilder

# Column ranges for realized trades sheet formatting (1-indexed Excel columns)
# Columns: ticker(1), currency(2), date(3), qty(4), gross_tcy(5), fees_tcy(6),
#          net_tcy(7), alloc_tcy(8), pl_tcy(9), gross_eur(10), fees_eur(11),
#          net_eur(12), alloc_eur(13), pl_eur(14), legs_json(15)
_REALIZED_TCY_MONEY_COLS = range(5, 10)  # Trade currency columns (gross..pl)
_REALIZED_EUR_MONEY_COLS = range(10, 15)  # EUR columns (gross..pl)

# Single canonical label table: one key set, with both locale strings co-located per
# field, so a locale cannot silently diverge (a field cannot exist in one language
# alone). _labels() projects the active locale; anything other than "EN" falls to "PT".
_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "sheet": {
        "summary": {"EN": "Trading Totals", "PT": "Totais de Operações"},
        "realized": {"EN": "Realized Trades", "PT": "Operações Realizadas"},
        "per_symbol": {"EN": "Per Symbol Summary", "PT": "Resumo por Símbolo"},
        "dividends": {"EN": "Dividends", "PT": "Dividendos"},
        "interest": {"EN": "Account Interest", "PT": "Juros da Conta"},
        "withholding": {"EN": "Withholding Tax", "PT": "Retenção na Fonte"},
        "transfers": {"EN": "Stock Transfers", "PT": "Transferências de Ações"},
        "anexo_j": {
            "EN": "Lot-Level EUR Breakdown",
            "PT": "Operações por Lote (Anexo J)",
        },
        "syep_interest": {"EN": "SYEP Interest", "PT": "Juros SYEP"},
    },
    "summary": {
        "metric": {"EN": "Metric", "PT": "Métrica"},
        "amount": {"EN": "Amount", "PT": "Montante"},
        "total_eur": {"EN": "Total Realized P/L (EUR)", "PT": "Total Realizado (EUR)"},
        "proceeds_eur": {
            "EN": "Total Net Proceeds (EUR)",
            "PT": "Total Proveitos Líquidos (EUR)",
        },
        "alloc_eur": {
            "EN": "Total Allocated Cost (EUR)",
            "PT": "Total Custo Alocado (EUR)",
        },
        "total_cur_tpl": {
            "EN": "Total Realized P/L ({cur})",
            "PT": "Total Realizado ({cur})",
        },
    },
    "realized": {
        "ticker": {"EN": "Ticker", "PT": "Símbolo"},
        "trade_currency": {"EN": "Trade Currency", "PT": "Moeda da Operação"},
        "sell_date": {"EN": "Sell Date", "PT": "Data de Venda"},
        "qty_sold": {"EN": "Quantity Sold", "PT": "Quantidade Vendida"},
        "gross_tcy": {
            "EN": "Gross Proceeds (Trade Currency)",
            "PT": "Proveitos Brutos (Moeda)",
        },
        "fees_tcy": {
            "EN": "Commissions/Fees (Trade Currency)",
            "PT": "Comissões/Taxas (Moeda)",
        },
        "net_tcy": {
            "EN": "Net Proceeds (Trade Currency)",
            "PT": "Proveitos Líquidos (Moeda)",
        },
        "alloc_tcy": {
            "EN": "Allocated Cost Basis (Trade Currency)",
            "PT": "Custo Alocado (Moeda)",
        },
        "pl_tcy": {
            "EN": "Realized P/L (Trade Currency)",
            "PT": "Resultado Realizado (Moeda)",
        },
        "gross_eur": {"EN": "Gross Proceeds (EUR)", "PT": "Proveitos Brutos (EUR)"},
        "fees_eur": {"EN": "Commissions/Fees (EUR)", "PT": "Comissões/Taxas (EUR)"},
        "net_eur": {"EN": "Net Proceeds (EUR)", "PT": "Proveitos Líquidos (EUR)"},
        "alloc_eur": {"EN": "Allocated Cost Basis (EUR)", "PT": "Custo Alocado (EUR)"},
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Resultado Realizado (EUR)"},
        "legs_json": {"EN": "Matched Buy Lots (JSON)", "PT": "Lotes de Compra (JSON)"},
        "gap_status": {"EN": "Basis Status", "PT": "Estado do Custo"},
    },
    "anexo_j": {
        "ticker": {"EN": "Ticker", "PT": "Símbolo"},
        "trade_currency": {"EN": "Trade Currency", "PT": "Moeda da Operação"},
        "buy_date": {"EN": "Acquisition Date", "PT": "Data de Aquisição"},
        "sell_date": {"EN": "Disposal Date", "PT": "Data de Venda"},
        "qty": {"EN": "Quantity", "PT": "Quantidade"},
        "alloc_eur": {
            "EN": "Acquisition Value (EUR)",
            "PT": "Valor de Aquisição (EUR)",
        },
        "proceeds_eur": {
            "EN": "Disposal Value (EUR)",
            "PT": "Valor de Realização (EUR)",
        },
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Mais/menos\u2011valia (EUR)"},
        "transferred": {"EN": "Transferred", "PT": "Transferido"},
        "synthetic": {"EN": "Synthetic", "PT": "Sintético"},
    },
    "per_symbol": {
        "ticker": {"EN": "Ticker", "PT": "Símbolo"},
        "trade_currency": {"EN": "Trade Currency", "PT": "Moeda da Operação"},
        "pl_tcy": {
            "EN": "Realized P/L (Trade Currency)",
            "PT": "Resultado Realizado (Moeda)",
        },
        "net_tcy": {
            "EN": "Net Proceeds (Trade Currency)",
            "PT": "Proveitos Líquidos (Moeda)",
        },
        "alloc_tcy": {
            "EN": "Allocated Cost Basis (Trade Currency)",
            "PT": "Custo Alocado (Moeda)",
        },
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Resultado Realizado (EUR)"},
        "net_eur": {"EN": "Net Proceeds (EUR)", "PT": "Proveitos Líquidos (EUR)"},
        "alloc_eur": {"EN": "Allocated Cost Basis (EUR)", "PT": "Custo Alocado (EUR)"},
        "has_gap": {"EN": "Gap / Synthetic", "PT": "Lacuna / Sintético"},
    },
    "dividends": {
        "date": {"EN": "Date", "PT": "Data"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "desc": {"EN": "Description", "PT": "Descrição"},
        "amount": {"EN": "Amount (Currency)", "PT": "Montante (Moeda)"},
        "amount_eur": {"EN": "Amount (EUR)", "PT": "Montante (EUR)"},
    },
    "interest": {
        "date": {"EN": "Date", "PT": "Data"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "desc": {"EN": "Description", "PT": "Descrição"},
        "amount": {"EN": "Amount (Currency)", "PT": "Montante (Moeda)"},
        "amount_eur": {"EN": "Amount (EUR)", "PT": "Montante (EUR)"},
    },
    "withholding": {
        "date": {"EN": "Date", "PT": "Data"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "desc": {"EN": "Description", "PT": "Descrição"},
        "type": {"EN": "Type", "PT": "Tipo"},
        "country": {"EN": "Country", "PT": "País"},
        "amount": {"EN": "Amount (Currency)", "PT": "Montante (Moeda)"},
        "amount_eur": {"EN": "Amount (EUR)", "PT": "Montante (EUR)"},
    },
    "syep": {
        "date": {"EN": "Value Date", "PT": "Data"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "symbol": {"EN": "Symbol", "PT": "Símbolo"},
        "start_date": {"EN": "Start Date", "PT": "Data de Início"},
        "quantity": {"EN": "Quantity", "PT": "Quantidade"},
        "collateral": {"EN": "Collateral Amount", "PT": "Valor de Colateral"},
        "market_rate": {"EN": "Market Rate (%)", "PT": "Taxa de Mercado (%)"},
        "customer_rate": {"EN": "Customer Rate (%)", "PT": "Taxa ao Cliente (%)"},
        "interest_paid": {
            "EN": "Interest Paid (Currency)",
            "PT": "Juros Pagos (Moeda)",
        },
        "interest_paid_eur": {"EN": "Interest Paid (EUR)", "PT": "Juros Pagos (EUR)"},
        "code": {"EN": "Code", "PT": "Código"},
    },
    "transfers": {
        "date": {"EN": "Date", "PT": "Data"},
        "symbol": {"EN": "Symbol", "PT": "Símbolo"},
        "direction": {"EN": "Direction", "PT": "Direção"},
        "quantity": {"EN": "Quantity", "PT": "Quantidade"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "market_value": {"EN": "Market Value", "PT": "Valor de Mercado"},
        "code": {"EN": "Code", "PT": "Código"},
    },
}


def _gap_status(rl: RealizedLine) -> str:
    """Human-readable basis provenance for a realized line's status column.

    Distinguishes a clean FIFO match (empty) from the two gap outcomes: a residual lot
    synthesized from IBKR's ``Basis`` versus an unmatched remainder left at zero cost.
    ``gap_fixed`` implies ``has_gap``, so it is checked first.
    """
    if not rl.has_gap:
        return ""
    if rl.gap_fixed:
        return "synthesized from Basis"
    return "zero-cost gap"


class ReportSink(Protocol):
    def write(self, report: ReportBuilder) -> Path:  # returns written file path
        ...


@dataclass
class ExcelReportSink:
    out_path: Path
    locale: str = "PT"  # "PT" (default) or "EN"

    @property
    def _date_format(self) -> str:
        """Excel date format string based on locale."""
        return "DD/MM/YYYY" if self.locale.upper() == "PT" else "YYYY-MM-DD"

    def _labels(self) -> dict[str, dict[str, str]]:
        """Project the canonical label table onto the active locale.

        Returns a ``{section: {field: text}}`` view for the selected locale; any locale
        other than "EN" falls back to "PT" (the report's default).
        """
        loc = "EN" if (self.locale or "PT").upper() == "EN" else "PT"
        return {
            section: {field: trans[loc] for field, trans in fields.items()}
            for section, fields in _LABELS.items()
        }

    def write(self, report: ReportBuilder) -> Path:
        out_path = Path(self.out_path)
        wb = Workbook()

        # Remove the default sheet
        ws_default = wb.active
        if ws_default is not None:
            wb.remove(ws_default)

        labels = self._labels()

        self._write_summary(wb, report, labels)
        self._write_realized(wb, report, labels)
        self._write_anexo_j(wb, report, labels)
        self._write_per_symbol(wb, report, labels)
        self._write_dividends(wb, report, labels)
        self._write_interest(wb, report, labels)
        self._write_syep_interest(wb, report, labels)
        self._write_withholding(wb, report, labels)
        self._write_transfers(wb, report, labels)

        for _ws in wb.worksheets:
            self._autosize(_ws)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out_path)
        return out_path

    def _write_summary(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        # Summary sheet (totals)
        ws = wb.create_sheet(title=labels["sheet"]["summary"])
        total_eur = sum(
            (rl.realized_pl_eur or Decimal("0") for rl in report.realized_lines),
            Decimal("0"),
        )
        proceeds_total_eur = sum(
            (rl.sell_net_eur or Decimal("0") for rl in report.realized_lines),
            Decimal("0"),
        )
        alloc_total_eur = sum(
            (rl.alloc_cost_eur or Decimal("0") for rl in report.realized_lines),
            Decimal("0"),
        )

        totals_by_cur: dict[str, Decimal] = {}
        for rl in report.realized_lines:
            # Exclude EUR from by-currency totals to avoid duplicate label confusion
            if rl.currency == "EUR":
                continue
            totals_by_cur[rl.currency] = (
                totals_by_cur.get(rl.currency, Decimal("0")) + rl.realized_pl_ccy
            )
        ws.append([labels["summary"]["metric"], labels["summary"]["amount"]])

        # Primary EUR totals
        ws.append([labels["summary"]["total_eur"], float(total_eur)])
        ws.cell(row=ws.max_row, column=2).number_format = self._money_fmt_for_currency(
            "EUR"
        )
        ws.append([labels["summary"]["proceeds_eur"], float(proceeds_total_eur)])
        ws.cell(row=ws.max_row, column=2).number_format = self._money_fmt_for_currency(
            "EUR"
        )
        ws.append([labels["summary"]["alloc_eur"], float(alloc_total_eur)])
        ws.cell(row=ws.max_row, column=2).number_format = self._money_fmt_for_currency(
            "EUR"
        )
        for cur, amt in sorted(totals_by_cur.items()):
            ws.append([labels["summary"]["total_cur_tpl"].format(cur=cur), float(amt)])
            ws.cell(
                row=ws.max_row, column=2
            ).number_format = self._money_fmt_for_currency(cur)

    def _write_realized(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        # Realized trades sheet
        ws = wb.create_sheet(title=labels["sheet"]["realized"])
        ws.append(
            [
                labels["realized"]["ticker"],
                labels["realized"]["trade_currency"],
                labels["realized"]["sell_date"],
                labels["realized"]["qty_sold"],
                labels["realized"]["gross_tcy"],
                labels["realized"]["fees_tcy"],
                labels["realized"]["net_tcy"],
                labels["realized"]["alloc_tcy"],
                labels["realized"]["pl_tcy"],
                labels["realized"]["gross_eur"],
                labels["realized"]["fees_eur"],
                labels["realized"]["net_eur"],
                labels["realized"]["alloc_eur"],
                labels["realized"]["pl_eur"],
                labels["realized"]["legs_json"],
                labels["realized"]["gap_status"],
            ]
        )

        date_fmt = self._date_format
        qty_fmt = "0.########"

        for rl in report.realized_lines:
            alloc_cost_ccy = sum((leg.alloc_cost_ccy for leg in rl.legs), Decimal("0"))
            legs_json = json.dumps(
                [
                    {
                        "buy_date": (ld.buy_date.isoformat() if ld.buy_date else None),
                        "qty": str(ld.qty),
                        "alloc_cost_ccy": str(ld.alloc_cost_ccy),
                    }
                    for ld in rl.legs
                ]
            )
            ws.append(
                [
                    rl.symbol,
                    rl.currency,
                    rl.sell_date,
                    float(rl.sell_qty),
                    float(rl.sell_gross_ccy),
                    float(rl.sell_comm_ccy),
                    float(rl.sell_net_ccy),
                    float(alloc_cost_ccy),
                    float(rl.realized_pl_ccy),
                    (None if rl.sell_gross_eur is None else float(rl.sell_gross_eur)),
                    (None if rl.sell_comm_eur is None else float(rl.sell_comm_eur)),
                    (None if rl.sell_net_eur is None else float(rl.sell_net_eur)),
                    (None if rl.alloc_cost_eur is None else float(rl.alloc_cost_eur)),
                    (None if rl.realized_pl_eur is None else float(rl.realized_pl_eur)),
                    legs_json,
                    _gap_status(rl),
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=3).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = qty_fmt
            tcy_fmt = self._money_fmt_for_currency(rl.currency)
            for c in _REALIZED_TCY_MONEY_COLS:
                ws.cell(row=r, column=c).number_format = tcy_fmt
            eur_fmt = self._money_fmt_for_currency("EUR")
            for c in _REALIZED_EUR_MONEY_COLS:
                ws.cell(row=r, column=c).number_format = eur_fmt

    def _write_anexo_j(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        # Annex J helper (per-leg breakdown with EUR values)
        ws = wb.create_sheet(title=labels["sheet"]["anexo_j"])
        ws.append(
            [
                labels["anexo_j"]["ticker"],
                labels["anexo_j"]["trade_currency"],
                labels["anexo_j"]["buy_date"],
                labels["anexo_j"]["sell_date"],
                labels["anexo_j"]["qty"],
                labels["anexo_j"]["alloc_eur"],
                labels["anexo_j"]["proceeds_eur"],
                labels["anexo_j"]["pl_eur"],
                labels["anexo_j"]["transferred"],
                labels["anexo_j"]["synthetic"],
            ]
        )

        date_fmt = self._date_format
        qty_fmt = "0.########"

        for rl in report.realized_lines:
            for leg in rl.legs:
                alloc_eur = leg.alloc_cost_eur
                proceeds_eur = leg.proceeds_share_eur
                pl_eur = None
                if alloc_eur is not None and proceeds_eur is not None:
                    pl_eur = quantize_money(proceeds_eur - alloc_eur)
                # Check if lot was from a transfer
                is_transferred = leg.transferred
                ws.append(
                    [
                        rl.symbol,
                        rl.currency,
                        leg.buy_date,
                        rl.sell_date,
                        float(leg.qty),
                        (None if alloc_eur is None else float(alloc_eur)),
                        (None if proceeds_eur is None else float(proceeds_eur)),
                        (None if pl_eur is None else float(pl_eur)),
                        "Yes" if is_transferred else "",
                        "Yes" if leg.synthetic else "",
                    ]
                )
                r = ws.max_row
                ws.cell(row=r, column=3).number_format = date_fmt
                ws.cell(row=r, column=4).number_format = date_fmt
                ws.cell(row=r, column=5).number_format = qty_fmt
                for c in (6, 7, 8):
                    ws.cell(
                        row=r, column=c
                    ).number_format = self._money_fmt_for_currency("EUR")

    def _write_per_symbol(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        # Per-symbol summary (trade currency + EUR)
        ws = wb.create_sheet(title=labels["sheet"]["per_symbol"])
        ws.append(
            [
                labels["per_symbol"]["ticker"],
                labels["per_symbol"]["trade_currency"],
                labels["per_symbol"]["pl_tcy"],
                labels["per_symbol"]["net_tcy"],
                labels["per_symbol"]["alloc_tcy"],
                labels["per_symbol"]["pl_eur"],
                labels["per_symbol"]["net_eur"],
                labels["per_symbol"]["alloc_eur"],
                labels["per_symbol"]["has_gap"],
            ]
        )

        # A symbol's per-symbol totals silently fold in any gap-filled or synthesized
        # line; flag the symbol so the aggregate isn't read as a clean FIFO result.
        gap_symbols = {rl.symbol for rl in report.realized_lines if rl.has_gap}

        # Invariant: each symbol maps to exactly one trade currency
        # (enforced by validate_symbol_currency_uniqueness at ingestion).
        for symbol, totals in sorted(report.symbol_totals.items()):
            ccy, ccy_totals = next(iter(totals.by_currency.items()))
            row = [
                symbol,
                ccy,
                float(ccy_totals.realized),
                float(ccy_totals.proceeds),
                float(ccy_totals.alloc_cost),
                float(totals.eur.realized),
                float(totals.eur.proceeds),
                float(totals.eur.alloc_cost),
                "Yes" if symbol in gap_symbols else "",
            ]
            ws.append(row)

            r = ws.max_row
            # Money formats for trade currency values
            tcy_fmt = self._money_fmt_for_currency(ccy)
            for c in (3, 4, 5):
                ws.cell(row=r, column=c).number_format = tcy_fmt
            for c in (6, 7, 8):
                ws.cell(row=r, column=c).number_format = self._money_fmt_for_currency(
                    "EUR"
                )

    def _write_dividends(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        if not report.dividends:
            return
        ws = wb.create_sheet(title=labels["sheet"]["dividends"])
        ws.append(
            [
                labels["dividends"]["date"],
                labels["dividends"]["currency"],
                labels["dividends"]["desc"],
                labels["dividends"]["amount"],
                labels["dividends"]["amount_eur"],
            ]
        )
        sorted_divs = sorted(report.dividends, key=lambda row: row.description.lower())
        date_fmt = self._date_format

        for d in sorted_divs:
            ws.append(
                [
                    d.date,
                    d.currency,
                    d.description,
                    float(d.amount),
                    (None if d.amount_eur is None else float(d.amount_eur)),
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=1).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = self._money_fmt_for_currency(
                d.currency
            )
            ws.cell(row=r, column=5).number_format = self._money_fmt_for_currency("EUR")

    def _write_interest(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        if not report.interest:
            return
        ws = wb.create_sheet(title=labels["sheet"]["interest"])
        ws.append(
            [
                labels["interest"]["date"],
                labels["interest"]["currency"],
                labels["interest"]["desc"],
                labels["interest"]["amount"],
                labels["interest"]["amount_eur"],
            ]
        )
        sorted_interest = sorted(
            report.interest,
            key=lambda row: row.description.lower(),
        )
        date_fmt = self._date_format

        for d in sorted_interest:
            ws.append(
                [
                    d.date,
                    d.currency,
                    d.description,
                    float(d.amount),
                    (None if d.amount_eur is None else float(d.amount_eur)),
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=1).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = self._money_fmt_for_currency(
                d.currency
            )
            ws.cell(row=r, column=5).number_format = self._money_fmt_for_currency("EUR")

    def _write_syep_interest(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        if not report.syep_interest:
            return
        ws = wb.create_sheet(title=labels["sheet"]["syep_interest"])
        ws.append(
            [
                labels["syep"]["date"],
                labels["syep"]["currency"],
                labels["syep"]["symbol"],
                labels["syep"]["start_date"],
                labels["syep"]["quantity"],
                labels["syep"]["collateral"],
                labels["syep"]["market_rate"],
                labels["syep"]["customer_rate"],
                labels["syep"]["interest_paid"],
                labels["syep"]["interest_paid_eur"],
                labels["syep"]["code"],
            ]
        )
        pct_fmt = "0.00####"
        date_fmt = self._date_format
        qty_fmt = "0.########"

        for row in report.syep_interest:
            ws.append(
                [
                    row.value_date,
                    row.currency,
                    row.symbol,
                    row.start_date,
                    float(row.quantity),
                    float(row.collateral_amount),
                    float(row.market_rate_pct),
                    float(row.customer_rate_pct),
                    float(row.interest_paid),
                    (
                        None
                        if row.interest_paid_eur is None
                        else float(row.interest_paid_eur)
                    ),
                    row.code,
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=1).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = date_fmt
            ws.cell(row=r, column=5).number_format = qty_fmt
            ws.cell(row=r, column=6).number_format = self._money_fmt_for_currency(
                row.currency
            )
            ws.cell(row=r, column=7).number_format = pct_fmt
            ws.cell(row=r, column=8).number_format = pct_fmt
            ws.cell(row=r, column=9).number_format = self._money_fmt_for_currency(
                row.currency
            )
            ws.cell(row=r, column=10).number_format = self._money_fmt_for_currency(
                "EUR"
            )

    def _write_withholding(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        if not report.withholding:
            return
        ws = wb.create_sheet(title=labels["sheet"]["withholding"])
        ws.append(
            [
                labels["withholding"]["date"],
                labels["withholding"]["currency"],
                labels["withholding"]["desc"],
                labels["withholding"]["type"],
                labels["withholding"]["country"],
                labels["withholding"]["amount"],
                labels["withholding"]["amount_eur"],
            ]
        )
        sorted_withholding = sorted(
            report.withholding,
            key=lambda row: (
                row.currency.upper(),
                row.description.lower(),
            ),
        )
        date_fmt = self._date_format

        for d in sorted_withholding:
            ws.append(
                [
                    d.date,
                    d.currency,
                    d.description,
                    d.type,
                    d.country,
                    float(d.amount),
                    (None if d.amount_eur is None else float(d.amount_eur)),
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=1).number_format = date_fmt
            ws.cell(row=r, column=6).number_format = self._money_fmt_for_currency(
                d.currency
            )
            ws.cell(row=r, column=7).number_format = self._money_fmt_for_currency("EUR")

    def _write_transfers(
        self, wb: Workbook, report: ReportBuilder, labels: dict[str, dict[str, str]]
    ) -> None:
        if not report.transfers:
            return
        ws = wb.create_sheet(title=labels["sheet"]["transfers"])
        ws.append(
            [
                labels["transfers"]["date"],
                labels["transfers"]["symbol"],
                labels["transfers"]["direction"],
                labels["transfers"]["quantity"],
                labels["transfers"]["currency"],
                labels["transfers"]["market_value"],
                labels["transfers"]["code"],
            ]
        )
        sorted_transfers = sorted(
            report.transfers,
            key=lambda t: (t.date, t.symbol),
        )
        date_fmt = self._date_format
        qty_fmt = "0.########"

        for t in sorted_transfers:
            ws.append(
                [
                    t.date,
                    t.symbol,
                    t.direction,
                    float(t.quantity),
                    t.currency,
                    float(t.market_value),
                    t.code,
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=1).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = qty_fmt
            ws.cell(row=r, column=6).number_format = self._money_fmt_for_currency(
                t.currency
            )

    def _money_fmt_for_currency(self, ccy: str) -> str:
        loc = self.locale.upper()
        cur = (ccy or "").upper()
        symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
        sym = symbols.get(cur)
        if sym:
            if cur == "EUR" and loc == "PT":
                return f'#,##0.00 "{sym}"'
            return f"{sym}#,##0.00"
        if loc == "PT":
            return f'#,##0.00 "{cur}"'
        return f'"{cur}" #,##0.00'

    def _autosize(
        self, sheet: Worksheet, max_width: int = 60, min_width: int = 10
    ) -> None:
        header_values = [cell.value for cell in sheet[1]] if sheet.max_row else []
        for col in range(1, sheet.max_column + 1):
            max_len = 0
            for row in range(1, sheet.max_row + 1):
                v = sheet.cell(row=row, column=col).value
                if v is None:
                    continue
                # Approximate display width using string conversion
                if hasattr(v, "strftime"):
                    s = (
                        v.strftime("%d/%m/%Y")
                        if self.locale.upper() == "PT"
                        else v.strftime("%Y-%m-%d")
                    )
                else:
                    s = str(v)
                if len(s) > max_len:
                    max_len = len(s)
            header = header_values[col - 1] if col - 1 < len(header_values) else None
            if header:
                max_len = max(max_len, len(str(header)))
            width = min(max_width, max(min_width, max_len + 2))
            if header and "JSON" in str(header):
                width = min(width, 50)
            sheet.column_dimensions[get_column_letter(col)].width = width
