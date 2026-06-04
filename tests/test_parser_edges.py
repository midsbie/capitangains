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


def test_empty_kind_statement_summary_line_is_skipped_not_errored():
    # IBKR Activity Statements contain standalone, statement-level summary lines whose
    # "kind" cell (column 1) is empty — e.g. "Total P/L for Statement Period". They
    # carry no subtable data and must be skipped WITHOUT failing the parse: an error
    # here makes the CLI abort (exit 2) and write no workbook.
    rows = [
        ["Trades", "Header", "Currency", "Symbol", "Quantity"],
        ["Trades", "Data", "EUR", "ASML", "10"],
        ["Total P/L for Statement Period", "", "", "", "-7856.59805361", ""],
    ]
    parser = IbkrStatementCsvParser()
    model, report = parser.parse_rows(rows)

    # Must not reach the has_errors condition cli.py checks before SystemExit(2).
    assert not report.has_errors

    # The summary line is skipped, not ingested: only the genuine Data row
    # remains under the active subtable.
    assert list(model.iter_rows("Trades")) == [
        {"Currency": "EUR", "Symbol": "ASML", "Quantity": "10"},
    ]


def test_empty_kind_summary_line_recorded_as_info():
    # Same skip as above, but now surfaced as an info-severity issue (visible at -v),
    # not dropped silently. has_errors must stay False so the CLI does not abort.
    rows = [
        ["Trades", "Header", "Currency", "Symbol", "Quantity"],
        ["Trades", "Data", "EUR", "ASML", "10"],
        ["Total P/L for Statement Period", "", "", "", "-7856.59", ""],
    ]
    _, report = IbkrStatementCsvParser().parse_rows(rows)

    infos = [i for i in report.issues if i.severity == "info"]
    assert len(infos) == 1
    assert infos[0].line_no == 3
    assert "summary line" in infos[0].message.lower()
    assert not report.has_errors


def test_short_data_rows_are_padded_and_aggregated_as_info():
    # Two short rows under one header -> a single aggregate info, not one per row
    # (avoids flooding on systematically narrow sections like the IBKR "Codes" legend).
    rows = [
        ["Trades", "Header", "Currency", "Symbol", "Quantity"],
        ["Trades", "Data", "EUR", "ASML"],  # one cell short of the header
        ["Trades", "Data", "USD", "AAPL"],  # also short
    ]
    model, report = IbkrStatementCsvParser().parse_rows(rows)

    infos = [i for i in report.issues if i.severity == "info"]
    assert len(infos) == 1
    assert "2 data row(s)" in infos[0].message
    assert "padded" in infos[0].message
    assert not report.has_errors
    # Padding fills the missing trailing cell with an empty string.
    assert list(model.iter_rows("Trades")) == [
        {"Currency": "EUR", "Symbol": "ASML", "Quantity": ""},
        {"Currency": "USD", "Symbol": "AAPL", "Quantity": ""},
    ]


def test_long_data_rows_are_trimmed_and_aggregated_as_warning():
    rows = [
        ["Trades", "Header", "Currency", "Symbol", "Quantity"],
        ["Trades", "Data", "EUR", "ASML", "10", "EXTRA"],  # one cell too many
    ]
    model, report = IbkrStatementCsvParser().parse_rows(rows)

    warnings = [i for i in report.issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "1 data row(s)" in warnings[0].message
    assert "dropped" in warnings[0].message
    # Trimming drops the trailing cell; this is a warning (data loss), not an error.
    assert not report.has_errors
    assert list(model.iter_rows("Trades")) == [
        {"Currency": "EUR", "Symbol": "ASML", "Quantity": "10"},
    ]
