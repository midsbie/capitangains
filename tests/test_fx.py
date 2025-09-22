import csv
import datetime as dt
from decimal import Decimal

import pytest

from capitangains.reporting.fx import FxTable


def _write_csv(tmp_path, rows):
    path = tmp_path / "fx.csv"
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["date", "currency", "rate"])
        writer.writerows(rows)
    return path


def test_fx_from_csv_parses_rates(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            ["2024-01-01", "USD", "1.25"],
            ["2024-01-02", "USD", "1.20"],
        ],
    )
    table = FxTable.from_csv(path)
    expected = Decimal("1") / Decimal("1.20")
    assert table.get_rate(dt.date(2024, 1, 2), "USD") == expected
    assert table.has_rate_exact(dt.date(2024, 1, 1), "USD") is True
    assert table.has_rate_exact(dt.date(2024, 1, 3), "USD") is False


def test_fx_from_csv_rejects_missing_columns(tmp_path):
    path = tmp_path / "fx_bad.csv"
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["date", "currency", "eur_per_unit"])
    with pytest.raises(ValueError):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_zero_rate(tmp_path):
    path = _write_csv(tmp_path, [["2024-01-01", "USD", "0"]])
    with pytest.raises(ValueError):
        FxTable.from_csv(path)


def test_fx_get_rate_weekend_fallback(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            ["2024-01-05", "USD", "1.20"],
        ],
    )
    table = FxTable.from_csv(path)
    # weekend fallback (2024-01-06) should use 2024-01-05 rate
    assert table.get_rate(dt.date(2024, 1, 6), "USD") == table.get_rate(
        dt.date(2024, 1, 5), "USD"
    )


def test_fx_get_rate_unknown_currency():
    table = FxTable()
    assert table.get_rate(dt.date(2024, 1, 1), "JPY") is None
    assert table.has_rate_exact(dt.date(2024, 1, 1), "JPY") is False
    assert table.get_rate(dt.date(2024, 1, 1), "EUR") == Decimal("1")
