"""Localization layer for the Excel report: the label catalog and its per-locale
projection, plus the matching locale-aware number formats.

LABELS co-locates both locale strings per field under one key set, so a locale cannot
silently diverge (a field cannot exist in one language alone). labels_for projects the
catalog onto the active locale; any locale other than "EN" falls back to "PT" (the
report's default). NumberFormats is the matching policy for numeric cells (date order
and currency presentation), which also vary by locale.
"""

from __future__ import annotations

from dataclasses import dataclass

LABELS: dict[str, dict[str, dict[str, str]]] = {
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
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Mais/menos‑valia (EUR)"},
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


def labels_for(locale: str) -> dict[str, dict[str, str]]:
    """Project the canonical label catalog onto the active locale.

    Returns a {section: {field: text}} view for the selected locale; any locale other
    than "EN" falls back to "PT" (the report's default).
    """
    loc = "EN" if (locale or "PT").upper() == "EN" else "PT"
    return {
        section: {field: trans[loc] for field, trans in fields.items()}
        for section, fields in LABELS.items()
    }


# Currency-to-symbol map for money number formats; defined once at module scope rather
# than rebuilt on every cell. Currencies absent here fall back to a quoted ISO code.
_CURRENCY_SYMBOLS: dict[str, str] = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


@dataclass(frozen=True)
class NumberFormats:
    """Locale-aware Excel number-format strings. Pure formatting policy depending only
    on the locale, kept separate from the workbook-writing sink so the column
    descriptors that consume it do not depend on ExcelReportSink.
    """

    locale: str

    @property
    def date(self) -> str:
        return "DD/MM/YYYY" if self.locale.upper() == "PT" else "YYYY-MM-DD"

    def money(self, ccy: str) -> str:
        loc = self.locale.upper()
        cur = (ccy or "").upper()
        sym = _CURRENCY_SYMBOLS.get(cur)
        if sym:
            if cur == "EUR" and loc == "PT":
                return f'#,##0.00 "{sym}"'
            return f"{sym}#,##0.00"
        if loc == "PT":
            return f'#,##0.00 "{cur}"'
        return f'"{cur}" #,##0.00'
