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

        # Summary sheet (totals)
        ws = wb.create_sheet(title="Summary")
        total_eur = sum(
            (rl.realized_pl_eur or Decimal("0") for rl in report.realized_lines),
            Decimal("0"),
        )
        totals_by_cur = {}
        for rl in report.realized_lines:
            totals_by_cur[rl.currency] = (
                totals_by_cur.get(rl.currency, Decimal("0")) + rl.realized_pl_ccy
            )
        ws.append(["Metric", "Amount"])
        if total_eur != 0:
            ws.append(["Total Realized P/L (EUR)", str(total_eur)])
        for cur, amt in sorted(totals_by_cur.items()):
            ws.append([f"Total Realized P/L ({cur})", str(amt)])

        # Realized trades sheet
        ws = wb.create_sheet(title="RealizedTrades")
        ws.append(
            [
                "Ticker",
                "Trade Currency",
                "Sell Date",
                "Quantity Sold",
                "Gross Proceeds (Trade Currency)",
                "Commissions/Fees (Trade Currency)",
                "Net Proceeds (Trade Currency)",
                "Allocated Cost Basis (Trade Currency)",
                "Realized P/L (Trade Currency)",
                "Gross Proceeds (EUR)",
                "Commissions/Fees (EUR)",
                "Net Proceeds (EUR)",
                "Allocated Cost Basis (EUR)",
                "Realized P/L (EUR)",
                "Matched Buy Lots (JSON)",
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
                    rl.sell_date.isoformat(),
                    str(rl.sell_qty),
                    str(rl.sell_gross_ccy),
                    str(rl.sell_comm_ccy),
                    str(rl.sell_net_ccy),
                    str(alloc_cost_ccy),
                    str(rl.realized_pl_ccy),
                    ("" if rl.sell_gross_eur is None else str(rl.sell_gross_eur)),
                    ("" if rl.sell_comm_eur is None else str(rl.sell_comm_eur)),
                    ("" if rl.sell_net_eur is None else str(rl.sell_net_eur)),
                    ("" if rl.alloc_cost_eur is None else str(rl.alloc_cost_eur)),
                    ("" if rl.realized_pl_eur is None else str(rl.realized_pl_eur)),
                    legs_json,
                ]
            )

        # Per-symbol summary
        ws = wb.create_sheet(title="PerSymbolSummary")
        # Collect currencies dynamically
        all_ccy = set()
        for _, totals in report.symbol_totals.items():
            for k in totals.keys():
                if k.startswith("realized_ccy:"):
                    all_ccy.add(k.split(":", 1)[1])
        headers = (
            ["Ticker"]
            + [f"Realized P/L ({c})" for c in sorted(all_ccy)]
            + [
                "Realized P/L (EUR)",
                "Net Proceeds (EUR)",
                "Allocated Cost Basis (EUR)",
            ]
        )
        ws.append(headers)
        for symbol, totals in sorted(report.symbol_totals.items()):
            row = [symbol]
            for c in sorted(all_ccy):
                row.append(str(totals.get("realized_ccy:" + c, Decimal("0"))))
            row.append(str(totals.get("realized_eur", Decimal("0"))))
            row.append(str(totals.get("proceeds_eur", Decimal("0"))))
            row.append(str(totals.get("alloc_cost_eur", Decimal("0"))))
            ws.append(row)

        # Dividends
        if report.dividends:
            ws = wb.create_sheet(title="Dividends")
            ws.append(["Date", "Currency", "Description", "Amount (Currency)"])
            for d in report.dividends:
                ws.append(
                    [
                        d["date"].isoformat(),
                        d["currency"],
                        d["description"],
                        str(d["amount"]),
                    ]
                )

        # Withholding Tax
        if report.withholding:
            ws = wb.create_sheet(title="WithholdingTax")
            ws.append(
                ["Date", "Currency", "Description", "Amount (Currency)", "Tax Code"]
            )
            for d in report.withholding:
                ws.append(
                    [
                        d["date"].isoformat(),
                        d["currency"],
                        d["description"],
                        str(d["amount"]),
                        d.get("code", ""),
                    ]
                )

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
