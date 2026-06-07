"""CLI input resolution and the --statements-dir auto-discovery path.

Exercises capitangains.cmd.cli.resolve_inputs (the hand-rolled either/or between
positional path(s) and --statements-dir, since a nargs="*" positional defeats an
argparse mutually-exclusive group) and main end to end:

- resolve_inputs exits 2 (parser.error) when both sources are given, neither is, the
  directory is not a directory, or discovery selects nothing.
- The discovery manifest is printed to stdout (distinct from the logger), and the
  selected statements' paths feed the pipeline.
- A+B seam: a corrupt (identity-unreadable) statement in the directory is selected by
  discovery, then halted by the pipeline's identity gate (Change A).
"""

import csv
import logging
import sys

import pytest

from capitangains.cmd.cli import build_argparser, main, resolve_inputs

_TRADES_HEADER = [
    "Trades",
    "Header",
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

_Y2023 = "January 1, 2023 - December 31, 2023"
_Y2024 = "January 1, 2024 - December 31, 2024"


def _write_statement(path, *, period, year, account="U1", currency="EUR"):
    rows = [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "Title", "Activity Statement"],
        ["Statement", "Data", "Period", period],
        ["Account Information", "Header", "Field Name", "Field Value"],
        ["Account Information", "Data", "Account", account],
        _TRADES_HEADER,
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            currency,
            "AAPL",
            f"{year}-02-10, 10:00:00",
            "10",
            "100",
            "-1000",
            "-1",
            "O",
            "1000",
            "0",
        ],
    ]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)


def _write_forex(path):
    rows = [["date", "currency", "rate"], ["2024-01-04", "USD", "1.10"]]
    with open(path, "w", newline="", encoding="utf-8") as fp:
        csv.writer(fp).writerows(rows)


def _resolve(argv):
    parser = build_argparser()
    return resolve_inputs(parser.parse_args(argv), parser)


# --- resolve_inputs: validation ------------------------------------------------------


def test_resolve_inputs_both_sources_exits_2(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(SystemExit) as exc:
        _resolve(["--year", "2024", "a.csv", "--statements-dir", str(d)])
    assert exc.value.code == 2


def test_resolve_inputs_no_source_exits_2():
    with pytest.raises(SystemExit) as exc:
        _resolve(["--year", "2024"])
    assert exc.value.code == 2


def test_resolve_inputs_non_directory_exits_2(tmp_path):
    not_a_dir = tmp_path / "not_a_dir.csv"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _resolve(["--year", "2024", "--statements-dir", str(not_a_dir)])
    assert exc.value.code == 2


def test_resolve_inputs_empty_discovery_exits_2(tmp_path):
    # A directory with no IBKR statements (only a forex csv) selects nothing; there is
    # nothing to report on, so it is fatal.
    d = tmp_path / "d"
    d.mkdir()
    _write_forex(d / "forex.csv")
    with pytest.raises(SystemExit) as exc:
        _resolve(["--year", "2024", "--statements-dir", str(d)])
    assert exc.value.code == 2


def test_resolve_inputs_positional_passthrough():
    assert _resolve(["--year", "2024", "a.csv", "b.csv"]) == ["a.csv", "b.csv"]


# --- resolve_inputs: discovery + manifest --------------------------------------------


def test_resolve_inputs_discovers_and_prints_manifest(tmp_path, capsys):
    d = tmp_path / "d"
    d.mkdir()
    _write_statement(d / "2023.csv", period=_Y2023, year=2023)
    _write_statement(d / "2024.csv", period=_Y2024, year=2024)
    _write_forex(d / "forex.csv")

    inputs = _resolve(["--year", "2024", "--statements-dir", str(d)])

    assert [p.rsplit("/", 1)[-1] for p in inputs] == ["2023.csv", "2024.csv"]
    out = capsys.readouterr().out
    assert "Selected 2 statement(s)" in out
    assert "Excluded 0 statement(s)" in out  # header printed even at count zero
    assert "Ignored 1 non-statement file(s)" in out
    assert "forex.csv" in out


# --- main: end to end ----------------------------------------------------------------


def test_main_statements_dir_writes_workbook(tmp_path, monkeypatch):
    # The selected statements feed the pipeline and a workbook is written. EUR converts
    # by identity, so no FX table is needed.
    d = tmp_path / "d"
    d.mkdir()
    _write_statement(d / "2024.csv", period=_Y2024, year=2024)
    out = tmp_path / "out.xlsx"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capitangains",
            "--year",
            "2024",
            "--statements-dir",
            str(d),
            "--output",
            str(out),
        ],
    )

    main()

    assert out.exists()


def test_main_statements_dir_corrupt_statement_halts(tmp_path, monkeypatch, caplog):
    # A+B seam: an identity-unreadable statement is selected by discovery (period=None),
    # then the pipeline's identity gate reports it and halts (exit 2, no workbook).
    d = tmp_path / "d"
    d.mkdir()
    _write_statement(d / "bad.csv", period="2024-01-01 to 2024-12-31", year=2024)
    out = tmp_path / "out.xlsx"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capitangains",
            "--year",
            "2024",
            "--statements-dir",
            str(d),
            "--output",
            str(out),
        ],
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert not out.exists()
    assert any(
        "missing or malformed identity" in r.getMessage() for r in caplog.records
    )
