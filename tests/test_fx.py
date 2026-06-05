import csv
import datetime as dt
import logging
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


def test_fx_short_fallback_logs_info_not_warning(tmp_path, caplog):
    # A <=7-day stale fallback is benign: it should surface at INFO (-v), not WARNING.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])  # Friday
    table = FxTable.from_csv(path)

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.fx"):
        rate = table.get_rate(dt.date(2024, 1, 8), "USD")  # Monday: 3 days back

    assert rate == table.get_rate(dt.date(2024, 1, 5), "USD")
    records = [r for r in caplog.records if "3 days earlier" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_fx_fallback_at_cap_boundary_is_returned(tmp_path):
    # Exactly _MAX_FX_LOOKBACK_DAYS (7) back is still fresh enough -> rate returned.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])
    table = FxTable.from_csv(path)
    assert table.get_rate(dt.date(2024, 1, 12), "USD") == table.get_rate(
        dt.date(2024, 1, 5), "USD"
    )


def test_fx_stale_fallback_beyond_cap_returns_none(tmp_path, caplog):
    # One day past the cap (8 days) is too stale: report missing, not an extrapolation.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])
    table = FxTable.from_csv(path)

    with caplog.at_level(logging.WARNING, logger="capitangains.reporting.fx"):
        rate = table.get_rate(dt.date(2024, 1, 13), "USD")  # 8 days back

    assert rate is None
    assert any("staleness cap" in r.getMessage() for r in caplog.records)


def test_fx_date_past_table_end_returns_none(tmp_path):
    # The "FX table ends in October, sale settles in December" case (finding #10,
    # defect B): a date far past the last entry must not silently reuse the last rate.
    path = _write_csv(tmp_path, [["2024-10-31", "USD", "1.20"]])
    table = FxTable.from_csv(path)
    assert table.get_rate(dt.date(2024, 12, 15), "USD") is None


def test_fx_from_csv_rejects_unpadded_date(tmp_path):
    # A non-canonical (unpadded) ISO date sorts lexically out of chronological order and
    # never matches a zero-padded lookup key. Finding #12 rejects it loudly at ingest
    # rather than store it as an untrusted string and silently misprice (or, post-#10,
    # spuriously report missing) every conversion that touches it.
    path = _write_csv(tmp_path, [["2024-1-5", "USD", "1.20"]])
    with pytest.raises(ValueError, match="unparseable date"):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_invalid_calendar_date(tmp_path):
    # A structurally-malformed date (month 13) is likewise rejected at ingest, not
    # stored as a key that lookup would have to defend against (finding #12).
    path = _write_csv(tmp_path, [["2024-13-01", "USD", "1.20"]])
    with pytest.raises(ValueError, match="unparseable date"):
        FxTable.from_csv(path)


def test_fx_from_csv_keys_on_real_dates(tmp_path):
    # Canonical dates are kept as dt.date keys: exact match and in-window fallback
    # resolve to the right rows. (The lexical-vs-chronological hazard is precluded
    # upstream by rejecting non-canonical dates at ingest, covered above.)
    path = _write_csv(
        tmp_path,
        [
            ["2024-01-05", "USD", "1.20"],
            ["2024-01-10", "USD", "1.10"],
        ],
    )
    table = FxTable.from_csv(path)
    assert table.get_rate(dt.date(2024, 1, 10), "USD") == Decimal("1") / Decimal("1.10")
    # 2024-01-07 falls back to the nearest prior observation, 2024-01-05.
    assert table.get_rate(dt.date(2024, 1, 7), "USD") == Decimal("1") / Decimal("1.20")
