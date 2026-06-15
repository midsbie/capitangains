"""discover_statements: classify a directory's *.csv by reporting period.

Pure discovery over ``sorted(directory.glob("*.csv"))``: an IBKR Activity Statement (it
carries a 'Statement' section) whose period starts in the year or earlier is selected; a
later one is excluded as future; a non-statement csv (no 'Statement' section) is
ignored. An IBKR-shaped file whose identity will not parse is selected with
``period=None`` -- never silently dropped -- so the pipeline's identity gate reports it.
"""

import csv

from capitangains.cmd.discovery import DiscoveryResult, discover_statements
from tests.support import (
    TRADES_COLUMNS,
    Y2023,
    Y2024,
    header_row,
    statement_meta_rows,
    trade_data,
    write_statement_csv,
)

_Y2025 = "January 1, 2025 - December 31, 2025"


def _write_statement(path, *, period, year, account="U1", currency="EUR"):
    # An IBKR-shaped statement: a 'Statement' section (the structural discriminator),
    # account/period identity, and a single opening BUY in `year`.
    rows = [
        *statement_meta_rows(account=account, period=period),
        header_row("Trades", TRADES_COLUMNS),
        trade_data(currency=currency, datetime_str=f"{year}-02-10, 10:00:00"),
    ]
    write_statement_csv(path, rows)


def _write_forex(path):
    # A non-statement csv: no 'Statement' section, so it is ignored. Shaped like the
    # forex-rate table the tool also accepts elsewhere (date,currency,rate).
    rows = [
        ["date", "currency", "rate"],
        ["2024-01-04", "USD", "1.10"],
    ]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)


def test_selects_year_and_earlier(tmp_path):
    _write_statement(tmp_path / "2023.csv", period=Y2023, year=2023)
    _write_statement(tmp_path / "2024.csv", period=Y2024, year=2024)

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["2023.csv", "2024.csv"]
    assert result.excluded_future == ()
    assert result.ignored == ()


def test_excludes_future(tmp_path):
    _write_statement(tmp_path / "2024.csv", period=Y2024, year=2024)
    _write_statement(tmp_path / "2025.csv", period=_Y2025, year=2025)

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["2024.csv"]
    assert [s.path.name for s in result.excluded_future] == ["2025.csv"]


def test_ignores_non_statement_csv(tmp_path):
    _write_statement(tmp_path / "2024.csv", period=Y2024, year=2024)
    _write_forex(tmp_path / "forex.csv")

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["2024.csv"]
    assert [p.name for p in result.ignored] == ["forex.csv"]


def test_unparseable_identity_is_selected_with_no_period(tmp_path):
    # IBKR-shaped (it has a 'Statement' section) but the Period will not parse: it
    # cannot be year-filtered, so it is selected with period=None rather than dropped.
    # The pipeline's identity gate, not discovery, reports it (the load-bearing edge).
    _write_statement(tmp_path / "bad.csv", period="2024-01-01 to 2024-12-31", year=2024)

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["bad.csv"]
    assert result.selected[0].period is None


def test_non_recursive_and_csv_only(tmp_path):
    # Only *.csv directly in the directory: a nested csv and a non-csv are both skipped.
    _write_statement(tmp_path / "2024.csv", period=Y2024, year=2024)
    nested = tmp_path / "nested"
    nested.mkdir()
    _write_statement(nested / "2023.csv", period=Y2023, year=2023)
    (tmp_path / "notes.txt").write_text("not a csv", encoding="utf-8")

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["2024.csv"]
    assert result.ignored == ()


def test_discovers_uppercase_csv_extension(tmp_path):
    # A statement exported as .CSV must be matched too: a case-sensitive *.csv glob
    # drops it from every bucket on a case-sensitive filesystem, with no diagnostic.
    _write_statement(tmp_path / "2024.CSV", period=Y2024, year=2024)

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == ["2024.CSV"]
    assert result.excluded_future == ()
    assert result.ignored == ()


def test_empty_directory(tmp_path):
    assert discover_statements(tmp_path, 2024) == DiscoveryResult(
        selected=(), excluded_future=(), ignored=()
    )


def test_selected_ordered_by_period_then_path(tmp_path):
    # selected is sorted by (period start, path); a period=None entry sorts last,
    # independent of its filename.
    _write_statement(tmp_path / "b_2024.csv", period=Y2024, year=2024)
    _write_statement(tmp_path / "a_2023.csv", period=Y2023, year=2023)
    _write_statement(
        tmp_path / "aaa_bad.csv", period="2024-01-01 to 2024-12-31", year=2024
    )

    result = discover_statements(tmp_path, 2024)

    assert [s.path.name for s in result.selected] == [
        "a_2023.csv",  # earliest period first
        "b_2024.csv",  # then the later period
        "aaa_bad.csv",  # identity-unreadable last, despite sorting first by name
    ]
