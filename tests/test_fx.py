import csv
import datetime as dt
import logging
from decimal import Decimal

import pytest

from capitangains.conv import Currency
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
    assert table.get_rate(dt.date(2024, 1, 2), Currency("USD")) == expected
    assert table.has_rate_exact(dt.date(2024, 1, 1), Currency("USD")) is True
    assert table.has_rate_exact(dt.date(2024, 1, 3), Currency("USD")) is False


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


def test_fx_from_csv_rejects_conflicting_duplicate(tmp_path):
    # A duplicate (currency, date) row with a different rate must not last-row-win
    # silently; it aborts like the other table-level validations.
    path = _write_csv(
        tmp_path,
        [
            ["2024-03-15", "USD", "1.20"],
            ["2024-03-15", "USD", "12.0"],
        ],
    )
    with pytest.raises(ValueError, match="duplicate row for USD"):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_matching_duplicate(tmp_path):
    # Even a duplicate that restates the identical rate is rejected: a process that
    # double-emits a key is untrustworthy regardless of whether the values agree.
    path = _write_csv(
        tmp_path,
        [
            ["2024-03-15", "USD", "1.20"],
            ["2024-03-15", "USD", "1.20"],
        ],
    )
    with pytest.raises(ValueError, match="duplicate row for USD"):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_comma_in_rate(tmp_path):
    # An operator-supplied rate with a comma is irrecoverably ambiguous (thousands
    # separator vs decimal comma), so it is rejected rather than silently stripped into
    # a ~10000x error.
    path = _write_csv(tmp_path, [["2024-01-01", "USD", "1,0850"]])
    with pytest.raises(ValueError, match="comma"):
        FxTable.from_csv(path)


def test_fx_from_csv_accepts_large_rate_without_separator(tmp_path):
    # A rate may legitimately exceed 1000 (e.g. ~17000 IDR per EUR); a plain decimal
    # with no grouping separator parses fine, so magnitude alone never implies a comma.
    path = _write_csv(tmp_path, [["2024-01-01", "IDR", "17000"]])
    table = FxTable.from_csv(path)
    expected = Decimal("1") / Decimal("17000")
    assert table.get_rate(dt.date(2024, 1, 1), Currency("IDR")) == expected


def test_fx_from_csv_rejects_infinite_rate(tmp_path):
    # "Infinity" constructs as a valid Decimal and slips the `<= 0` positive-rate gate
    # (Infinity is not <= 0); 1/Infinity then silently yields a 0 EUR-per-unit rate that
    # zeroes every figure in the currency. A non-finite rate must abort, not convert.
    path = _write_csv(tmp_path, [["2024-01-01", "USD", "Infinity"]])
    with pytest.raises(ValueError):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_nan_rate(tmp_path):
    # "NaN" also constructs as a valid Decimal; today it reaches the `<= 0` gate and
    # raises a raw decimal.InvalidOperation (a NaN traps on ordering comparison), an
    # uncaught crash. It must instead fail closed as a clean domain ValueError.
    path = _write_csv(tmp_path, [["2024-01-01", "USD", "NaN"]])
    with pytest.raises(ValueError):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_whitespace_in_rate(tmp_path):
    # Internal whitespace is silently stripped by the IBKR-grammar cleaner just like a
    # comma, so "1 0850" becomes 10850: the same ~10000x misprice the comma guard exists
    # to prevent. Operator rates declare no locale, so reject it outright.
    path = _write_csv(tmp_path, [["2024-01-01", "USD", "1 0850"]])
    with pytest.raises(ValueError, match="whitespace"):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_row_missing_rate(tmp_path):
    # A short row leaves row["rate"] as None. It must surface as a clean ValueError (the
    # same precondition failure as any other unparseable cell), not a raw TypeError from
    # testing `"," in None`.
    path = _write_csv(tmp_path, [["2024-01-01", "USD"]])
    with pytest.raises(ValueError):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_row_missing_currency(tmp_path):
    # A row missing the currency cell leaves row["currency"] as None. It must surface
    # as a clean, row-located ValueError, not a raw AttributeError from Currency(None)
    # running None.strip().upper() in its normalizing __post_init__.
    path = _write_csv(tmp_path, [["2024-01-01"]])
    with pytest.raises(ValueError, match="missing currency"):
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
    assert table.get_rate(dt.date(2024, 1, 6), Currency("USD")) == table.get_rate(
        dt.date(2024, 1, 5), Currency("USD")
    )


def test_fx_get_rate_unknown_currency():
    table = FxTable()
    assert table.get_rate(dt.date(2024, 1, 1), Currency("JPY")) is None
    assert table.has_rate_exact(dt.date(2024, 1, 1), Currency("JPY")) is False
    assert table.get_rate(dt.date(2024, 1, 1), Currency("EUR")) == Decimal("1")


def test_fx_short_fallback_logs_info_not_warning(tmp_path, caplog):
    # A <=7-day stale fallback is benign: it should surface at INFO (-v), not WARNING.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])  # Friday
    table = FxTable.from_csv(path)

    with caplog.at_level(logging.INFO, logger="capitangains.reporting.fx"):
        rate = table.get_rate(
            dt.date(2024, 1, 8), Currency("USD")
        )  # Monday: 3 days back

    assert rate == table.get_rate(dt.date(2024, 1, 5), Currency("USD"))
    records = [r for r in caplog.records if "3 days earlier" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_fx_fallback_at_cap_boundary_is_returned(tmp_path):
    # Exactly _MAX_FX_LOOKBACK_DAYS (7) back is still fresh enough -> rate returned.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])
    table = FxTable.from_csv(path)
    assert table.get_rate(dt.date(2024, 1, 12), Currency("USD")) == table.get_rate(
        dt.date(2024, 1, 5), Currency("USD")
    )


def test_fx_stale_fallback_beyond_cap_returns_none(tmp_path, caplog):
    # One day past the cap (8 days) is too stale: report missing, not an extrapolation.
    path = _write_csv(tmp_path, [["2024-01-05", "USD", "1.20"]])
    table = FxTable.from_csv(path)

    with caplog.at_level(logging.WARNING, logger="capitangains.reporting.fx"):
        rate = table.get_rate(dt.date(2024, 1, 13), Currency("USD"))  # 8 days back

    assert rate is None
    assert any("staleness cap" in r.getMessage() for r in caplog.records)


def test_fx_date_past_table_end_returns_none(tmp_path):
    # The "FX table ends in October, sale settles in December" case: a date far past the
    # last entry must not silently reuse the last rate.
    path = _write_csv(tmp_path, [["2024-10-31", "USD", "1.20"]])
    table = FxTable.from_csv(path)
    assert table.get_rate(dt.date(2024, 12, 15), Currency("USD")) is None


def test_fx_from_csv_rejects_unpadded_date(tmp_path):
    # A non-canonical (unpadded) ISO date sorts lexically out of chronological order and
    # never matches a zero-padded lookup key. It is rejected loudly at ingest rather
    # than stored as an untrusted string that would silently misprice every conversion
    # that touches it (or, given that dates past the table end now return missing, make
    # them spuriously report missing instead).
    path = _write_csv(tmp_path, [["2024-1-5", "USD", "1.20"]])
    with pytest.raises(ValueError, match="unparseable date"):
        FxTable.from_csv(path)


def test_fx_from_csv_rejects_invalid_calendar_date(tmp_path):
    # A structurally-malformed date (month 13) is likewise rejected at ingest, not
    # stored as a key that lookup would have to defend against.
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
    assert table.get_rate(dt.date(2024, 1, 10), Currency("USD")) == Decimal(
        "1"
    ) / Decimal("1.10")
    # 2024-01-07 falls back to the nearest prior observation, 2024-01-05.
    assert table.get_rate(dt.date(2024, 1, 7), Currency("USD")) == Decimal(
        "1"
    ) / Decimal("1.20")
