"""Canonical IBKR section-name constants and the consumed/ignored coverage sets.

One home for the exact section-name each extractor keys on, so a rename is a one-line
edit and CONSUMED_SECTIONS cannot drift from the extractors. The coverage sweep
(reporting.validation.detect_unrecognized_sections) partitions every section present in
a merged statement into: consumed by an extractor, intentionally ignored
(known-irrelevant), or unrecognized; the last being what it warns on.
"""

# Sections each extractor consumes (the exact literal each passes to the model).
SEC_DIVIDENDS = "Dividends"
SEC_INTEREST = "Interest"
SEC_SYEP = "Stock Yield Enhancement Program Securities Lent Interest Details"
SEC_TRADES = "Trades"
SEC_TRANSFERS = "Transfers"
SEC_WITHHOLDING = "Withholding Tax"

CONSUMED_SECTIONS: frozenset[str] = frozenset(
    {SEC_TRADES, SEC_DIVIDENDS, SEC_INTEREST, SEC_WITHHOLDING, SEC_TRANSFERS, SEC_SYEP}
)

# Sections present in IBKR statements that this tool knowingly does not row-extract. Per
# the known-and-unused -> whitelist rule, every observed unconsumed section is listed
# here so the sweep stays quiet on familiar input and warns only on a name it has never
# seen. Statement / Account Information are consumed by parse_statement_metadata for
# identity (not row data), so they are listed here to keep the row-coverage sweep quiet.
IGNORED_SECTIONS: frozenset[str] = frozenset(
    {
        "Account Information",
        "Cash Report",
        "Change in Dividend Accruals",
        "Change in NAV",
        "Codes",
        "Corporate Actions",
        "Deposits & Withdrawals",
        "Fees",
        "Financial Instrument Information",
        "Forex Balances",
        "Interest Accruals",
        "Mark-to-Market Performance Summary",
        "Net Asset Value",
        "Net Stock Position Summary",
        "Notes/Legal Notes",
        "Open Positions",
        "Realized & Unrealized Performance Summary",
        "Statement",
        "Stock Yield Enhancement Program Securities Lent Activity",
        "Stock Yield Enhancement Program Securities Lent",
        "Total P/L for Statement Period",
        "Transaction Fees",
    }
)
