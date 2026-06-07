"""Unit tests for the read-side orchestrator, IbkrActivityStatementSource.

The source is the dual of ExcelReportSink: it runs every section extractor over one
merged model and returns a ParsedStatement (typed rows plus the union of row-level
defects). These tests pin its three observable contracts -- aggregation, defect-union
order, and asset_scope passthrough -- plus its purity (no logging, no SystemExit;
reporting and exit stay at the CLI boundary).
"""

import logging
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from capitangains.reporting import IbkrActivityStatementSource, ParsedStatement
from tests.support import parse_model

_SYEP_SECTION = "Stock Yield Enhancement Program Securities Lent Interest Details"

_TRADE_HEADER = [
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
]
_TRANSFER_HEADER = [
    "Transfers",
    "Header",
    "Asset Category",
    "Currency",
    "Symbol",
    "Date",
    "Direction",
    "Qty",
    "Market Value",
    "Code",
]
_DIVIDEND_HEADER = ["Dividends", "Header", "Currency", "Date", "Description", "Amount"]
_WITHHOLDING_HEADER = [
    "Withholding Tax",
    "Header",
    "Currency",
    "Date",
    "Description",
    "Amount",
    "Code",
]
_INTEREST_HEADER = ["Interest", "Header", "Currency", "Date", "Description", "Amount"]
_SYEP_HEADER = [
    _SYEP_SECTION,
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
]


def _trade(asset, symbol, *, qty="10"):
    return [
        "Trades",
        "Data",
        asset,
        "USD",
        symbol,
        "2024-01-10, 10:00:00",
        qty,
        "100",
        "-1000",
        "-1",
        "O",
        "1000",
        "0",
    ]


def test_read_aggregates_all_sections():
    model = parse_model(
        [
            _TRADE_HEADER,
            _trade("Stocks", "AAPL"),
            _TRANSFER_HEADER,
            [
                "Transfers",
                "Data",
                "Stocks",
                "USD",
                "MSFT",
                "2024-02-10",
                "In",
                "5",
                "500",
                "",
            ],
            _DIVIDEND_HEADER,
            ["Dividends", "Data", "EUR", "2024-01-05", "Div ABC", "10.00"],
            _WITHHOLDING_HEADER,
            [
                "Withholding Tax",
                "Data",
                "USD",
                "2024-01-06",
                "ABC Cash Dividend - US Tax",
                "-1.50",
                "",
            ],
            _INTEREST_HEADER,
            ["Interest", "Data", "USD", "2024-01-07", "Credit Interest", "2.00"],
            _SYEP_HEADER,
            [
                _SYEP_SECTION,
                "Data",
                "USD",
                "2024-01-15",
                "AAPL",
                "2024-01-01",
                "1000",
                "150000.00",
                "5.50",
                "4.75",
                "195.83",
                "SL",
            ],
        ]
    )

    parsed = IbkrActivityStatementSource().read(model)

    assert isinstance(parsed, ParsedStatement)
    # Every section field is an immutable snapshot (tuple), not a list.
    assert all(
        isinstance(field, tuple)
        for field in (
            parsed.trades,
            parsed.transfers,
            parsed.dividends,
            parsed.withholding,
            parsed.syep_interest,
            parsed.interest,
            parsed.defects,
        )
    )

    assert [t.symbol for t in parsed.trades] == ["AAPL"]
    assert [t.symbol for t in parsed.transfers] == ["MSFT"]
    assert [d.description for d in parsed.dividends] == ["Div ABC"]
    assert [w.amount for w in parsed.withholding] == [Decimal("-1.50")]
    assert [i.description for i in parsed.interest] == ["Credit Interest"]
    assert [s.symbol for s in parsed.syep_interest] == ["AAPL"]
    assert parsed.defects == ()


def test_read_unions_defects_in_call_order():
    # An in-scope trade with a non-numeric Quantity and a complete dividend row with a
    # non-numeric Amount each yield one row-level defect (neither survives into its
    # section). The trade extractor runs before the dividend one, so the union lists
    # Trades before Dividends.
    model = parse_model(
        [
            _TRADE_HEADER,
            _trade("Stocks", "BAD", qty="xyz"),
            _DIVIDEND_HEADER,
            ["Dividends", "Data", "EUR", "2024-03-03", "Bad Div", "xyz"],
        ]
    )

    parsed = IbkrActivityStatementSource().read(model)

    assert len(parsed.defects) == 2
    assert [d.section for d in parsed.defects] == ["Trades", "Dividends"]
    assert parsed.trades == ()
    assert parsed.dividends == ()


def test_read_asset_scope_passthrough():
    model = parse_model(
        [
            _TRADE_HEADER,
            _trade("Stocks", "AAPL"),
            _trade("Bonds", "BND"),
        ]
    )

    # The default (stocks_etfs) matches the app policy the CLI now states explicitly;
    # it drops the bond.
    default = IbkrActivityStatementSource().read(model)
    assert [t.symbol for t in default.trades] == ["AAPL"]

    # The knob forwards to the trades extractor: "all" keeps every asset category.
    widened = IbkrActivityStatementSource(asset_scope="all").read(model)
    assert {t.symbol for t in widened.trades} == {"AAPL", "BND"}


def test_read_is_pure(caplog):
    model = parse_model([_TRADE_HEADER, _trade("Stocks", "BAD", qty="xyz")])

    # Reaching the assertions at all proves read raised no SystemExit on the bad row.
    with caplog.at_level(logging.DEBUG):
        parsed = IbkrActivityStatementSource().read(model)

    assert [d.section for d in parsed.defects] == ["Trades"]
    # The source owns no logger; defect reporting (and the exit) belong to the boundary.
    assert [
        r for r in caplog.records if r.name == "capitangains.reporting.source"
    ] == []


def test_parsed_statement_is_frozen():
    parsed = IbkrActivityStatementSource().read(parse_model([]))
    with pytest.raises(FrozenInstanceError):
        # The assignment is the point of the test; mypy flags it statically, which is
        # exactly the guarantee being exercised at runtime here.
        parsed.trades = ()  # type: ignore[misc]
