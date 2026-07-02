"""Localization layer for the Excel report: the label catalog and its per-locale
projection, plus the matching locale-aware number formats.

LABELS co-locates both locale strings per field under one key set, so a locale cannot
silently diverge (a field cannot exist in one language alone). labels_for projects the
catalog onto a resolved locale. NumberFormats is the matching policy for numeric cells
(date order and currency presentation), which also vary by locale. canonical_locale is
the sole parse point from a raw str to a Locale; both labels_for and NumberFormats take
that resolved Locale and never re-validate, so labels and formats can never disagree (an
empty locale defaults to PT for both, an unsupported one is rejected at the boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

# The closed set of supported locales. canonical_locale is the sole parse point from a
# raw str to this type; everything downstream (labels_for, NumberFormats) carries a
# resolved Locale and never re-validates, so an unsupported value cannot reach them.
Locale = Literal["EN", "PT"]

LABELS: dict[str, dict[str, dict[str, str]]] = {
    "sheet": {
        "cover": {"EN": "Cover", "PT": "Capa"},
        "summary": {"EN": "Trading Totals", "PT": "Totais de Operações"},
        "realized": {"EN": "Realized Trades", "PT": "Operações Realizadas"},
        "per_symbol": {"EN": "Per Symbol Summary", "PT": "Resumo por Símbolo"},
        "dividends": {"EN": "Dividends", "PT": "Dividendos"},
        "interest": {"EN": "Account Interest", "PT": "Juros da Conta"},
        "withholding": {"EN": "Withholding Tax", "PT": "Retenção na Fonte"},
        "transfers": {"EN": "Stock Transfers", "PT": "Transferências de Ações"},
        "anexo_j": {"EN": "Annex J - Box 9.2A", "PT": "Anexo J - Quadro 9.2A"},
        "quadro_8a": {"EN": "Annex J - Box 8A", "PT": "Anexo J - Quadro 8A"},
        "syep_interest": {"EN": "SYEP Interest", "PT": "Juros SYEP"},
        "reconciliation": {"EN": "IBKR Reconciliation", "PT": "Reconciliação IBKR"},
    },
    "cover": {
        "account": {"EN": "Account", "PT": "Conta"},
        "tax_year": {"EN": "Tax Year", "PT": "Ano Fiscal"},
        "period_start": {"EN": "Statement Period From", "PT": "Período do Extrato De"},
        "period_end": {"EN": "Statement Period To", "PT": "Período do Extrato Até"},
        "fx_source": {"EN": "FX Rate Source", "PT": "Fonte das Taxas de Câmbio"},
        "fx_currencies": {"EN": "FX Currencies", "PT": "Moedas com Câmbio"},
        "fx_from": {"EN": "FX Rates From", "PT": "Câmbios De"},
        "fx_to": {"EN": "FX Rates To", "PT": "Câmbios Até"},
        "tool_version": {"EN": "Tool Version", "PT": "Versão da Ferramenta"},
        "generated_at": {"EN": "Generated", "PT": "Gerado em"},
        # Value shown for the FX fields when the run was given no FX table.
        "fx_none": {"EN": "None", "PT": "Nenhuma"},
        "index_sheet": {"EN": "Sheet", "PT": "Folha"},
        "index_purpose": {"EN": "Purpose", "PT": "Finalidade"},
    },
    # One-line purpose per sheet, keyed by sheet key, listed in the Cover index.
    "cover_purpose": {
        "summary": {
            "EN": "Headline realized totals for the year, in EUR.",
            "PT": "Totais realizados do ano, em EUR.",
        },
        "anexo_j": {
            "EN": "Capital gains for Anexo J Quadro 9.2A, per lot (EUR).",
            "PT": "Mais-valias para o Anexo J Quadro 9.2A, por lote (EUR).",
        },
        "quadro_8a": {
            "EN": "Capital income for Anexo J Quadro 8A (dividends, interest).",
            "PT": "Rendimentos de capitais do Anexo J Quadro 8A (dividendos, juros).",
        },
        "reconciliation": {
            "EN": "Trade-currency tie-out of realized P/L to IBKR.",
            "PT": "Conferência em moeda da operação com a IBKR.",
        },
        "realized": {
            "EN": "Every realized sale, trade-currency and EUR side by side.",
            "PT": "Cada venda realizada, em moeda da operação e em EUR.",
        },
        "per_symbol": {
            "EN": "Realized totals per symbol.",
            "PT": "Totais realizados por símbolo.",
        },
        "dividends": {
            "EN": "Dividend payments received.",
            "PT": "Dividendos recebidos.",
        },
        "interest": {
            "EN": "Account interest received.",
            "PT": "Juros da conta recebidos.",
        },
        "syep_interest": {
            "EN": "Stock-lending (SYEP) interest received.",
            "PT": "Juros de empréstimo de títulos (SYEP).",
        },
        "withholding": {
            "EN": "Foreign tax withheld at source.",
            "PT": "Imposto retido na fonte no estrangeiro.",
        },
        "transfers": {
            "EN": "Share transfers in and out.",
            "PT": "Transferências de ações (entradas e saídas).",
        },
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
        "gross_tcy": {"EN": "Gross Proceeds", "PT": "Proveitos Brutos"},
        "fees_tcy": {"EN": "Commissions/Fees", "PT": "Comissões/Taxas"},
        "net_tcy": {"EN": "Net Proceeds", "PT": "Proveitos Líquidos"},
        "alloc_tcy": {"EN": "Allocated Cost Basis", "PT": "Custo Alocado"},
        "pl_tcy": {"EN": "Realized P/L", "PT": "Resultado Realizado"},
        "gross_eur": {"EN": "Gross Proceeds (EUR)", "PT": "Proveitos Brutos (EUR)"},
        "fees_eur": {"EN": "Commissions/Fees (EUR)", "PT": "Comissões/Taxas (EUR)"},
        "net_eur": {"EN": "Net Proceeds (EUR)", "PT": "Proveitos Líquidos (EUR)"},
        "alloc_eur": {"EN": "Allocated Cost Basis (EUR)", "PT": "Custo Alocado (EUR)"},
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Resultado Realizado (EUR)"},
        "fx_rate": {"EN": "FX Rate", "PT": "Câmbio"},
        "gap_status": {"EN": "Basis Status", "PT": "Estado do Custo"},
        # Localized Basis Status row values (not headers), keyed by _gap_status_key.
        "gap_synthesized": {
            "EN": "Cost basis from broker statement, unverified",
            "PT": "Custo do extrato da corretora, não verificado",
        },
        "gap_zero_cost": {
            "EN": "Cost basis missing, treated as zero",
            "PT": "Custo em falta, considerado zero",
        },
    },
    "anexo_j": {
        # Fileable block (Anexo J Quadro 9.2A field order).
        "source_country": {"EN": "Source Country", "PT": "País da Fonte"},
        "code": {"EN": "Code", "PT": "Código"},
        "buy_year": {"EN": "Acq. Year", "PT": "Ano Aquisição"},
        "buy_month": {"EN": "Acq. Month", "PT": "Mês Aquisição"},
        "sell_year": {"EN": "Disp. Year", "PT": "Ano Realização"},
        "sell_month": {"EN": "Disp. Month", "PT": "Mês Realização"},
        "despesas": {"EN": "Expenses (EUR)", "PT": "Despesas (EUR)"},
        "counterparty_country": {
            "EN": "Counterparty Country",
            "PT": "País da Contraparte",
        },
        # Day of the Realizacao/Aquisicao dates, plus the two operator-supplied Portal
        # fields (foreign tax on the gain, admitted-to-trading flag) the sink leaves
        # blank in their Portal position.
        "sell_day": {"EN": "Disp. Day", "PT": "Dia Realização"},
        "buy_day": {"EN": "Acq. Day", "PT": "Dia Aquisição"},
        "foreign_tax": {
            "EN": "Foreign Tax (EUR)",
            "PT": "Imposto Pago no Estrangeiro (EUR)",
        },
        "admitted": {"EN": "Admitted to Trading?", "PT": "Admitido à Negociação?"},
        # Working block.
        "ticker": {"EN": "Ticker", "PT": "Símbolo"},
        "trade_currency": {"EN": "Trade Currency", "PT": "Moeda da Operação"},
        "buy_date": {"EN": "Acquisition Date", "PT": "Data de Aquisição"},
        "sell_date": {"EN": "Disposal Date", "PT": "Data de Venda"},
        "qty": {"EN": "Quantity", "PT": "Quantidade"},
        # The acquisition-side rate (buy_rate): it prices this leg's Acquisition Value.
        # Qualified to distinguish it from the Realized sheet's disposal-rate FX column.
        "fx_rate": {"EN": "FX Rate (Acq.)", "PT": "Câmbio (Aquisição)"},
        "alloc_eur": {
            "EN": "Acquisition Value (EUR)",
            "PT": "Valor de Aquisição (EUR)",
        },
        "realization_eur": {
            "EN": "Realization Value (EUR)",
            "PT": "Valor de Realização (EUR)",
        },
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Mais/menos-valia (EUR)"},
        "transferred": {"EN": "Transferred", "PT": "Transferido"},
        "synthetic": {"EN": "Estimated Basis", "PT": "Custo Estimado"},
    },
    "quadro_8a": {
        "income_code": {"EN": "Income Code", "PT": "Código Rendimento"},
        "kind": {"EN": "Type", "PT": "Tipo"},
        "country": {"EN": "Source Country", "PT": "País da Fonte"},
        "gross_eur": {"EN": "Gross Income (EUR)", "PT": "Rendimento Bruto (EUR)"},
        "tax_eur": {
            "EN": "Foreign Tax (EUR)",
            "PT": "Imposto Pago no Estrangeiro (EUR)",
        },
        # Localized values for the Type column (row values, not a header), keyed by
        # IncomeKind.label_key.
        "kind_dividend": {"EN": "Dividend", "PT": "Dividendo"},
        "kind_pil": {"EN": "Payment in Lieu", "PT": "Pagamento em Substituição"},
        "kind_interest": {"EN": "Interest", "PT": "Juros"},
    },
    "reconciliation": {
        "symbol": {"EN": "Symbol", "PT": "Símbolo"},
        "currency": {"EN": "Currency", "PT": "Moeda"},
        "mine": {"EN": "This Report", "PT": "Este Relatório"},
        "ibkr": {"EN": "IBKR", "PT": "IBKR"},
        "diff": {"EN": "Difference", "PT": "Diferença"},
        "status": {"EN": "Status", "PT": "Estado"},
        # Localized Status row values (not headers), keyed by _ReconRow.status_key.
        "status_ok": {"EN": "OK", "PT": "OK"},
        "status_mismatch": {"EN": "Mismatch", "PT": "Divergência"},
        "status_sign_flip": {"EN": "Sign Flip", "PT": "Sinal Invertido"},
        "status_synthetic": {
            "EN": "Synthesized, unconfirmed",
            "PT": "Sintetizado, não confirmado",
        },
        "status_incomplete": {
            "EN": "Not cross-checked (Forex)",
            "PT": "Não verificado (Forex)",
        },
        "status_anomalous": {
            "EN": "Anomalous, no IBKR P/L",
            "PT": "Anómalo, sem P/L IBKR",
        },
    },
    "per_symbol": {
        "ticker": {"EN": "Ticker", "PT": "Símbolo"},
        "trade_currency": {"EN": "Trade Currency", "PT": "Moeda da Operação"},
        "pl_tcy": {"EN": "Realized P/L", "PT": "Resultado Realizado"},
        "net_tcy": {"EN": "Net Proceeds", "PT": "Proveitos Líquidos"},
        "alloc_tcy": {"EN": "Allocated Cost Basis", "PT": "Custo Alocado"},
        "pl_eur": {"EN": "Realized P/L (EUR)", "PT": "Resultado Realizado (EUR)"},
        "net_eur": {"EN": "Net Proceeds (EUR)", "PT": "Proveitos Líquidos (EUR)"},
        "alloc_eur": {"EN": "Allocated Cost Basis (EUR)", "PT": "Custo Alocado (EUR)"},
        "has_gap": {"EN": "Estimated Basis", "PT": "Custo Estimado"},
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


# Derived from Locale so the runtime set and the type can never drift: add a locale to
# the Literal and it is supported here automatically.
SUPPORTED_LOCALES: tuple[Locale, ...] = get_args(Locale)
DEFAULT_LOCALE: Locale = "PT"  # the report's default, used for an empty/unset locale


def canonical_locale(locale: str) -> Locale:
    """Parse a raw locale string into a resolved Locale.

    An empty/unset locale falls back to DEFAULT_LOCALE. A non-empty locale outside
    SUPPORTED_LOCALES is a caller error and raises, rather than being silently coerced:
    the tool cannot produce, say, a German workbook, and quietly substituting Portuguese
    would be the kind of guess this codebase refuses to make. The CLI already constrains
    --locale to the supported set; this guards the programmatic boundary, where
    ExcelReportSink accepts a bare str.

    This is the sole parse point: callers resolve once here and pass the Locale down, so
    labels and formats can never disagree and never re-validate.
    """
    if not locale:
        return DEFAULT_LOCALE

    normalized = locale.upper()
    for supported in SUPPORTED_LOCALES:
        if normalized == supported:
            return supported

    raise ValueError(
        f"Unsupported locale {locale!r}; supported locales are "
        f"{', '.join(SUPPORTED_LOCALES)}."
    )


def labels_for(locale: Locale) -> dict[str, dict[str, str]]:
    """Project the canonical label catalog onto an already-resolved locale.

    Returns a {section: {field: text}} view. The locale is trusted (resolve a raw string
    through canonical_locale before calling); this does not re-validate.
    """
    return {
        section: {field: trans[locale] for field, trans in fields.items()}
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

    Holds an already-resolved Locale (see canonical_locale), so the formats are a direct
    lookup with no re-validation.
    """

    locale: Locale

    @property
    def date(self) -> str:
        return "DD/MM/YYYY" if self.locale == "PT" else "YYYY-MM-DD"

    def money(self, ccy: str) -> str:
        cur = (ccy or "").upper()
        sym = _CURRENCY_SYMBOLS.get(cur)
        if sym:
            if cur == "EUR" and self.locale == "PT":
                return f'#,##0.00 "{sym}"'
            return f"{sym}#,##0.00"

        if self.locale == "PT":
            return f'#,##0.00 "{cur}"'
        return f'"{cur}" #,##0.00'
