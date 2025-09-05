from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .report_builder import ReportBuilder


class ReportSink(Protocol):
    def write(self, report: ReportBuilder) -> Path:  # returns written file path
        ...


@dataclass
class ExcelReportSink:
    out_path: Path
    locale: str = "PT"  # "PT" (default) or "EN"

    def _labels(self):
        loc = (self.locale or "PT").upper()
        if loc == "EN":
            return {
                "sheet": {
                    "summary": "Summary",
                    "realized": "Realized Trades",
                    "per_symbol": "Per Symbol Summary",
                    "dividends": "Dividends",
                    "withholding": "Withholding Tax",
                },
                "summary": {
                    "metric": "Metric",
                    "amount": "Amount",
                    "total_eur": "Total Realized P/L (EUR)",
                    "total_cur_tpl": "Total Realized P/L ({cur})",
                },
                "realized": {
                    "ticker": "Ticker",
                    "trade_currency": "Trade Currency",
                    "sell_date": "Sell Date",
                    "qty_sold": "Quantity Sold",
                    "gross_tcy": "Gross Proceeds (Trade Currency)",
                    "fees_tcy": "Commissions/Fees (Trade Currency)",
                    "net_tcy": "Net Proceeds (Trade Currency)",
                    "alloc_tcy": "Allocated Cost Basis (Trade Currency)",
                    "pl_tcy": "Realized P/L (Trade Currency)",
                    "gross_eur": "Gross Proceeds (EUR)",
                    "fees_eur": "Commissions/Fees (EUR)",
                    "net_eur": "Net Proceeds (EUR)",
                    "alloc_eur": "Allocated Cost Basis (EUR)",
                    "pl_eur": "Realized P/L (EUR)",
                    "legs_json": "Matched Buy Lots (JSON)",
                },
                "per_symbol": {
                    "ticker": "Ticker",
                    "pl_tpl": "Realized P/L ({cur})",
                    "pl_eur": "Realized P/L (EUR)",
                    "net_eur": "Net Proceeds (EUR)",
                    "alloc_eur": "Allocated Cost Basis (EUR)",
                },
                "dividends": {
                    "date": "Date",
                    "currency": "Currency",
                    "desc": "Description",
                    "amount": "Amount (Currency)",
                },
                "withholding": {
                    "date": "Date",
                    "currency": "Currency",
                    "desc": "Description",
                    "amount": "Amount (Currency)",
                    "code": "Tax Code",
                },
            }
        # Default: Portuguese (Portugal)
        return {
            "sheet": {
                "summary": "Resumo",
                "realized": "Operações Realizadas",
                "per_symbol": "Resumo por Símbolo",
                "dividends": "Dividendos",
                "withholding": "Retenção na Fonte",
            },
            "summary": {
                "metric": "Métrica",
                "amount": "Montante",
                "total_eur": "Total Realizado (EUR)",
                "total_cur_tpl": "Total Realizado ({cur})",
            },
            "realized": {
                "ticker": "Símbolo",
                "trade_currency": "Moeda da Operação",
                "sell_date": "Data de Venda",
                "qty_sold": "Quantidade Vendida",
                "gross_tcy": "Proveitos Brutos (Moeda)",
                "fees_tcy": "Comissões/Taxas (Moeda)",
                "net_tcy": "Proveitos Líquidos (Moeda)",
                "alloc_tcy": "Custo Alocado (Moeda)",
                "pl_tcy": "Resultado Realizado (Moeda)",
                "gross_eur": "Proveitos Brutos (EUR)",
                "fees_eur": "Comissões/Taxas (EUR)",
                "net_eur": "Proveitos Líquidos (EUR)",
                "alloc_eur": "Custo Alocado (EUR)",
                "pl_eur": "Resultado Realizado (EUR)",
                "legs_json": "Lotes de Compra (JSON)",
            },
            "per_symbol": {
                "ticker": "Símbolo",
                "pl_tpl": "Resultado Realizado ({cur})",
                "pl_eur": "Resultado Realizado (EUR)",
                "net_eur": "Proveitos Líquidos (EUR)",
                "alloc_eur": "Custo Alocado (EUR)",
            },
            "dividends": {
                "date": "Data",
                "currency": "Moeda",
                "desc": "Descrição",
                "amount": "Montante (Moeda)",
            },
            "withholding": {
                "date": "Data",
                "currency": "Moeda",
                "desc": "Descrição",
                "amount": "Montante (Moeda)",
                "code": "Código de Imposto",
            },
        }

    def write(self, report: ReportBuilder) -> Path:
        try:
            from openpyxl import Workbook
        except Exception as e:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "openpyxl is required to write XLSX. Install with: pip install openpyxl"
            ) from e

        out_path = Path(self.out_path)
        wb = Workbook()

        # Remove the default sheet
        ws_default = wb.active
        wb.remove(ws_default)

        labels = self._labels()

        # Summary sheet (totals)
        ws = wb.create_sheet(title=labels["sheet"]["summary"])
        total_eur = sum(
            (rl.realized_pl_eur or Decimal("0") for rl in report.realized_lines),
            Decimal("0"),
        )
        totals_by_cur = {}
        for rl in report.realized_lines:
            totals_by_cur[rl.currency] = (
                totals_by_cur.get(rl.currency, Decimal("0")) + rl.realized_pl_ccy
            )
        ws.append([labels["summary"]["metric"], labels["summary"]["amount"]])
        # Number formats
        date_fmt = "DD/MM/YYYY" if self.locale.upper() == "PT" else "YYYY-MM-DD"
        money_fmt = "#,##0.00"
        qty_fmt = "0.########"
        if total_eur != 0:
            ws.append([labels["summary"]["total_eur"], float(total_eur)])
            ws.cell(row=ws.max_row, column=2).number_format = money_fmt
        for cur, amt in sorted(totals_by_cur.items()):
            ws.append([labels["summary"]["total_cur_tpl"].format(cur=cur), float(amt)])
            ws.cell(row=ws.max_row, column=2).number_format = money_fmt

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
            ]
        )
        import json

        for rl in report.realized_lines:
            alloc_cost_ccy = sum(
                (leg["alloc_cost_ccy"] for leg in rl.legs), Decimal("0")
            )
            legs_json = json.dumps(
                [
                    {
                        "buy_date": (
                            ld["buy_date"].isoformat() if ld["buy_date"] else None
                        ),
                        "qty": str(ld["qty"]),
                        "alloc_cost_ccy": str(ld["alloc_cost_ccy"]),
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
                ]
            )
            r = ws.max_row
            ws.cell(row=r, column=3).number_format = date_fmt
            ws.cell(row=r, column=4).number_format = qty_fmt
            for c in range(5, 15):
                ws.cell(row=r, column=c).number_format = money_fmt

        # Per-symbol summary
        ws = wb.create_sheet(title=labels["sheet"]["per_symbol"])
        # Collect currencies dynamically
        all_ccy = set()
        for _, totals in report.symbol_totals.items():
            for k in totals.keys():
                if k.startswith("realized_ccy:"):
                    all_ccy.add(k.split(":", 1)[1])
        headers = (
            [labels["per_symbol"]["ticker"]]
            + [labels["per_symbol"]["pl_tpl"].format(cur=c) for c in sorted(all_ccy)]
            + [
                labels["per_symbol"]["pl_eur"],
                labels["per_symbol"]["net_eur"],
                labels["per_symbol"]["alloc_eur"],
            ]
        )
        ws.append(headers)
        for symbol, totals in sorted(report.symbol_totals.items()):
            row = [symbol]
            for c in sorted(all_ccy):
                row.append(float(totals.get("realized_ccy:" + c, Decimal("0"))))
            row.append(float(totals.get("realized_eur", Decimal("0"))))
            row.append(float(totals.get("proceeds_eur", Decimal("0"))))
            row.append(float(totals.get("alloc_cost_eur", Decimal("0"))))
            ws.append(row)
            r = ws.max_row
            for c in range(2, 2 + len(all_ccy) + 3):
                ws.cell(row=r, column=c).number_format = money_fmt

        # Dividends
        if report.dividends:
            ws = wb.create_sheet(title=labels["sheet"]["dividends"])
            ws.append(
                [
                    labels["dividends"]["date"],
                    labels["dividends"]["currency"],
                    labels["dividends"]["desc"],
                    labels["dividends"]["amount"],
                ]
            )
            for d in report.dividends:
                ws.append(
                    [d["date"], d["currency"], d["description"], float(d["amount"])]
                )
                r = ws.max_row
                ws.cell(row=r, column=1).number_format = date_fmt
                ws.cell(row=r, column=4).number_format = money_fmt

        # Withholding Tax
        if report.withholding:
            ws = wb.create_sheet(title=labels["sheet"]["withholding"])
            ws.append(
                [
                    labels["withholding"]["date"],
                    labels["withholding"]["currency"],
                    labels["withholding"]["desc"],
                    labels["withholding"]["amount"],
                    labels["withholding"]["code"],
                ]
            )
            for d in report.withholding:
                ws.append(
                    [
                        d["date"],
                        d["currency"],
                        d["description"],
                        float(d["amount"]),
                        d.get("code", ""),
                    ]
                )
                r = ws.max_row
                ws.cell(row=r, column=1).number_format = date_fmt
                ws.cell(row=r, column=4).number_format = money_fmt

        out_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out_path)
        return out_path


@dataclass
class OdsReportSink:
    out_path: Path

    def write(self, report: ReportBuilder) -> Path:  # pragma: no cover - placeholder
        raise NotImplementedError(
            "ODS output not implemented yet. Consider using XLSX or extending ReportSink."
        )
