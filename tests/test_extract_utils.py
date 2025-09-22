import datetime as dt
from decimal import Decimal

from capitangains.model.ibkr import IbkrStatementCsvParser
from capitangains.reporting.extract import (
    parse_dividends,
    parse_interest,
    parse_syep_interest_details,
    parse_trades_stocklike,
    parse_withholding_tax,
)


def _parse_rows(rows):
    parser = IbkrStatementCsvParser()
    model, _ = parser.parse_rows(rows)
    return model


def test_parse_trades_scope_and_ordering():
    rows = [
        [
            "Trades",
            "Header",
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
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "AAA",
            "2024-01-02, 10:00:00",
            "10",
            "100",
            "-1000",
            "-1",
            "P",
            "",
            "",
        ],
        [
            "Trades",
            "Data",
            "ETF",
            "USD",
            "BBB",
            "2024-01-01, 12:00:00",
            "-5",
            "200",
            "1000",
            "-0.5",
            "P",
            "-1000",
            "5",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "CCC",
            "2024-01-02, 10:00:00",
            "-10",
            "100",
            "1000",
            "-1",
            "P",
            "-1000",
            "10",
        ],
        [
            "Trades",
            "Data",
            "Stocks",
            "USD",
            "IGNORED",
            "2024-01-03, 09:00:00",
            "0",
            "0",
            "0",
            "0",
            "P",
            "",
            "",
        ],
    ]
    model = _parse_rows(rows)

    trades = parse_trades_stocklike(model, asset_scope="stocks_etfs")
    assert [t.symbol for t in trades] == ["BBB", "AAA", "CCC"]
    assert trades[0].basis_ccy == Decimal("-1000")
    assert trades[0].realized_pl_ccy == Decimal("5")

    etf_only = parse_trades_stocklike(model, asset_scope="etfs")
    assert [t.symbol for t in etf_only] == ["BBB"]

    all_assets = parse_trades_stocklike(model, asset_scope="all")
    assert len(all_assets) == 3


def test_parse_dividends_and_withholding_classification():
    rows = [
        ["Dividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", " USD ", "2024-01-05", " Test Div ", "10.00"],
        ["Dividends", "Data", "", "", "", ""],
        [
            "Withholding Tax",
            "Header",
            "Currency",
            "Date",
            "Description",
            "Amount",
            "Code",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-06",
            "Cash Dividend - US Tax",
            "-1.50",
            "WHT",
        ],
        [
            "Withholding Tax",
            "Data",
            "USD",
            "2024-01-07",
            "Interest on Cash",
            "-0.50",
            "INT",
        ],
    ]
    model = _parse_rows(rows)

    dividends = parse_dividends(model)
    assert dividends == [
        {
            "currency": "USD",
            "date": dt.date(2024, 1, 5),
            "description": "Test Div",
            "amount": Decimal("10.00"),
        }
    ]

    withholding = parse_withholding_tax(model)
    assert withholding[0]["type"] == "Dividend"
    assert withholding[0]["country"] == "US"
    assert withholding[1]["type"] == "Interest"


def test_parse_syep_interest_skips_totals_and_coerces_numbers():
    rows = [
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Header",
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
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "USD",
            "2024-02-01",
            "ABC",
            "2024-01-15",
            "-100",
            "1000",
            "0.1",
            "0.05",
            "1.23",
            "Po",
        ],
        [
            "Stock Yield Enhancement Program Securities Lent Interest Details",
            "Data",
            "Total",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ],
    ]
    model = _parse_rows(rows)

    result = parse_syep_interest_details(model)
    assert len(result) == 1
    row = result[0]
    assert row["quantity"] == Decimal("-100")
    assert row["market_rate_pct"] == Decimal("0.1")
    assert row["value_date"] == dt.date(2024, 2, 1)


def test_parse_interest_skips_totals():
    rows = [
        ["Interest", "Header", "Currency", "Date", "Description", "Amount"],
        ["Interest", "Data", "USD", "2024-02-05", "Monthly Interest", "1.23"],
        ["Interest", "Data", "Total", "", "", "100"],
    ]
    model = _parse_rows(rows)

    interest = parse_interest(model)
    assert len(interest) == 1
    assert interest[0]["amount"] == Decimal("1.23")
