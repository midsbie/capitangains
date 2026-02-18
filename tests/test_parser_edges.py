from decimal import Decimal

from capitangains.model.ibkr import IbkrStatementCsvParser


def test_parser_bom_and_data_before_header():
    rows = [
        # Data before any header -> skipped
        [
            "Trades",
            "Data",
            "Stocks",
            "EUR",
            "ASML",
            "2024-01-10, 10:00:00",
            "-1",
            "100",
            "-100",
            "-1",
            "",
        ],
        # Header with BOM in section name
        ["\ufeffDividends", "Header", "Currency", "Date", "Description", "Amount"],
        ["Dividends", "Data", "EUR", "2024-01-05", "Test Div", "10.00"],
    ]
    parser = IbkrStatementCsvParser()
    model, report = parser.parse_rows(rows)

    # Data-before-header is skipped and reported as error
    assert any(
        i.severity == "error" and "Data row encountered before any header" in i.message
        for i in report.issues
    )

    # BOM-stripped section key should be 'Dividends'
    subs = model.get_subtables("Dividends")
    assert len(subs) == 1
    r = next(iter(model.iter_rows("Dividends")))
    assert r["Currency"] == "EUR" and Decimal(r["Amount"]) == Decimal("10.00")


def test_total_and_subtotal_rows_are_silently_skipped():
    rows = [
        ["Trades", "Header", "Currency", "Symbol", "Quantity"],
        ["Trades", "Data", "EUR", "ASML", "10"],
        ["Trades", "SubTotal", "", "", "10"],
        ["Trades", "Total", "", "", "10"],
    ]
    parser = IbkrStatementCsvParser()
    model, report = parser.parse_rows(rows)

    assert list(model.iter_rows("Trades")) == [
        {"Currency": "EUR", "Symbol": "ASML", "Quantity": "10"},
    ]
    assert not report.issues
