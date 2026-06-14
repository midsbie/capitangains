"""CSV-wire layer: build the raw row lists an IBKR Activity Statement CSV is made of.

The IbkrStatementCsvParser consumes ``[section, kind, *cells]`` rows. This module owns
the one canonical column set per section (so the trade header can no longer drift
between files), the ``[section, "Header"|"Data", ...]`` assemblers, and a CSV writer.

Named ``statement_rows`` rather than ``csv`` on purpose: a submodule literally named
``csv`` would shadow the stdlib ``csv`` that production modules import.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

# Payload columns only (the parser prepends [section, kind]); see header_row.  The trade
# header carries DataDiscriminator even though the extractor never reads it; keeping one
# canonical shape removes the with/without-discriminator drift the suite had
# accumulated.
TRADES_COLUMNS = [
    "DataDiscriminator",
    "Asset Category",
    "Currency",
    "Symbol",
    "Date/Time",
    "Quantity",
    "T. Price",
    "Proceeds",
    "Comm/Fee",
    "Code",
    "Basis",
    "Realized P/L",
]
TRANSFERS_COLUMNS = [
    "Asset Category",
    "Currency",
    "Symbol",
    "Date",
    "Direction",
    "Qty",
    "Market Value",
    "Code",
]
DIVIDENDS_COLUMNS = INTEREST_COLUMNS = ["Currency", "Date", "Description", "Amount"]
WITHHOLDING_COLUMNS = ["Currency", "Date", "Description", "Amount", "Code"]

SYEP_SECTION = "Stock Yield Enhancement Program Securities Lent Interest Details"
SYEP_COLUMNS = [
    "Currency",
    "Value Date",
    "Symbol",
    "Start Date",
    "Quantity",
    "Collateral Amount",
    "Market-based Rate (%)",
    "Interest Rate on Customer Collateral (%)",
    "Interest Paid to Customer",
    "Code",
]

STATEMENT_COLUMNS = ACCOUNT_COLUMNS = ["Field Name", "Field Value"]

# Full-year reporting periods in the IBKR month-name 'Period' format.
Y2023 = "January 1, 2023 - December 31, 2023"
Y2024 = "January 1, 2024 - December 31, 2024"


def header_row(section: str, columns: Sequence[str]) -> list[str]:
    """Build a ``[section, "Header", *columns]`` row."""
    return [section, "Header", *columns]


def section_table(
    section: str, columns: Sequence[str], *data_rows: Sequence[str]
) -> list[list[str]]:
    """Build a section's header row followed by one ``Data`` row per payload list."""
    rows = [header_row(section, columns)]
    rows.extend([section, "Data", *row] for row in data_rows)
    return rows


def trade_data(
    *,
    discriminator: str = "Order",
    asset_category: str = "Stocks",
    currency: str = "USD",
    symbol: str = "AAPL",
    datetime_str: str = "2024-01-10, 10:00:00",
    quantity: str = "10",
    t_price: str = "100",
    proceeds: str = "-1000",
    comm_fee: str = "-1",
    code: str = "O",
    basis: str = "1000",
    realized: str = "0",
) -> list[str]:
    """Build a full Trades ``Data`` row matching TRADES_COLUMNS (with discriminator)."""
    return [
        "Trades",
        "Data",
        discriminator,
        asset_category,
        currency,
        symbol,
        datetime_str,
        quantity,
        t_price,
        proceeds,
        comm_fee,
        code,
        basis,
        realized,
    ]


def transfer_data(
    *,
    asset_category: str = "Stocks",
    currency: str = "USD",
    symbol: str = "AAPL",
    date: str = "2024-01-01",
    direction: str = "In",
    quantity: str = "10",
    market_value: str = "1000",
    code: str = "",
) -> list[str]:
    """Build a full Transfers ``Data`` row matching TRANSFERS_COLUMNS."""
    return [
        "Transfers",
        "Data",
        asset_category,
        currency,
        symbol,
        date,
        direction,
        quantity,
        market_value,
        code,
    ]


def statement_meta_rows(*, account: str, period: str) -> list[list[str]]:
    """Build the Statement + Account Information identity block."""
    return [
        header_row("Statement", STATEMENT_COLUMNS),
        ["Statement", "Data", "Title", "Activity Statement"],
        ["Statement", "Data", "Period", period],
        header_row("Account Information", ACCOUNT_COLUMNS),
        ["Account Information", "Data", "Account", account],
    ]


def write_statement_csv(path: str | Path, rows: Sequence[Sequence[str]]) -> Path:
    """Write rows to a CSV file and return its path."""
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)
    return Path(path)
