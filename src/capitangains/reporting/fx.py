from __future__ import annotations

import bisect
import csv
import datetime as dt
import logging
import re
from collections import defaultdict
from decimal import Decimal, DivisionByZero
from pathlib import Path

from capitangains.conv import Currency
from capitangains.conv.numeric import to_decimal_strict

logger = logging.getLogger(__name__)

# Hard cap on fallback-rate staleness. The nearest prior observation is accepted
# only within this window (covers weekends/holidays); a wider gap makes get_rate return
# None so the rate is reported missing rather than silently extrapolated.
_MAX_FX_LOOKBACK_DAYS = 7

# An operator FX rate must be a plain decimal-point number; a comma or any interior
# whitespace is ambiguous digit grouping (a thousands separator vs a decimal mark,
# ~10000x apart). This is fx's own precondition on operator input, deliberately
# independent of the IBKR-number cleaner in conv.ibkr: the character overlap with its
# NUM_CLEAN_RE is incidental, so the two must not be coupled. See _parse_fx_rate.
_AMBIGUOUS_GROUPING_RE = re.compile(r"[,\s]")


def _parse_fx_rate(raw: str | None, ccy: Currency, date: dt.date) -> Decimal:
    """Parse one operator-supplied FX rate strictly, rejecting ambiguous digit grouping.

    The --fx-table CSV declares no locale, so a comma or internal whitespace in the
    rate is irrecoverably ambiguous: "1,0850" (or "1 0850") could be 10850 (a thousands
    group) or 1.0850 (a decimal mark), readings that differ by ~10000x. Magnitude cannot
    disambiguate, since a rate may legitimately exceed 1000 (e.g. ~17000 IDR per EUR).
    The rate multiplies every foreign figure and a wrong-but-positive value trips no
    later guard, so guessing would silently misprice the whole report. A rate is always
    expressible as a plain decimal-point number, so reject any grouping outright rather
    than pick an interpretation (re-reading the comma as a decimal mark would just move
    the silent error onto US-formatted input). A comma and whitespace are also what the
    IBKR-grammar cleaner silently strips from statement numbers, which is exactly why an
    operator rate must not be run through that cleaner; this guard rejects them first.
    With grouping excluded, to_decimal_strict handles the rest (sign, decimal point,
    non-finite, and the missing/malformed cases).
    """
    # Guard on an actual string: a short CSV row leaves row["rate"] as None, which would
    # make the membership test raise a raw TypeError. The None, empty, non-finite, and
    # malformed cases are what to_decimal_strict reports as a clean domain ValueError,
    # so let them fall through to it. Leading and trailing whitespace is benign
    # (to_decimal_strict trims it), so test the trimmed token for an interior comma or
    # whitespace.
    if isinstance(raw, str) and _AMBIGUOUS_GROUPING_RE.search(raw.strip()):
        raise ValueError(
            f"FX rate {raw!r} for {ccy} on {date} contains a comma or whitespace; the "
            f"FX table must use a plain decimal point with no digit grouping (a comma "
            f"or space is ambiguous between a thousands separator and a decimal mark)."
        )
    return to_decimal_strict(raw)


class FxTable:
    """Date-indexed FX table: (date, currency) -> EUR per 1 unit of currency.

    Accepted CSV schema (base currency is EUR):
      date,currency,rate   where rate = target_currency_units_per_EUR
    """

    def __init__(self) -> None:
        # Map: Currency -> { date -> Decimal(eur_per_unit) }, plus a sorted date list.
        # Currency keys are normalized, so lookups are case-insensitive. Dates are real
        # dt.date keys (not strings), so comparison and bisect are chronological, immune
        # to the lexical-vs-chronological hazard a string key would introduce.
        self.data: dict[Currency, dict[dt.date, Decimal]] = defaultdict(dict)
        self.date_index: dict[Currency, list[dt.date]] = {}

    @classmethod
    def from_csv(cls, path: str | Path) -> FxTable:
        inst = cls()
        with open(path, encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            fields = set(reader.fieldnames or [])
            if not {"date", "currency"}.issubset(fields):
                missing = {"date", "currency"} - fields
                raise ValueError(f"FX table missing columns: {sorted(missing)}")

            if "rate" not in fields:
                raise ValueError("FX table must contain 'rate' (units per EUR) column")

            for row in reader:
                raw_date = row["date"]
                try:
                    d = dt.date.fromisoformat(raw_date)
                except (ValueError, TypeError) as exc:
                    # Reject a malformed date here rather than store an untrusted string
                    # and mis-sort it later. A valid ISO date is a precondition.
                    raise ValueError(
                        f"FX table has an unparseable date {raw_date!r}"
                    ) from exc
                ccy = Currency(row["currency"])
                if not ccy.code:
                    raise ValueError(f"FX row missing currency for date {d}")
                if ccy.is_base:
                    eur_per_unit = Decimal("1")
                else:
                    units_per_eur = _parse_fx_rate(row["rate"], ccy, d)
                    if units_per_eur <= 0:
                        raise ValueError(
                            f"Encountered non-positive FX rate {units_per_eur} for "
                            f"{ccy} on {d}"
                        )
                    try:
                        eur_per_unit = Decimal("1") / units_per_eur
                    except DivisionByZero as exc:  # defensive, though checked above
                        raise ValueError(
                            f"Invalid zero FX rate for {ccy} on {d}"
                        ) from exc

                # A duplicate (currency, date) row means the upstream process double-
                # emitted a key; a process that does so cannot be trusted with the rest
                # of the table, so reject outright like the loader's other validations.
                if d in inst.data[ccy]:
                    raise ValueError(f"FX table has a duplicate row for {ccy} on {d}")

                inst.data[ccy][d] = eur_per_unit

        for ccy, m in inst.data.items():
            inst.date_index[ccy] = sorted(m.keys())

        if logger.isEnabledFor(logging.DEBUG):
            all_currencies = set(inst.data.keys())
            logger.debug(
                "Loaded FX rates for %d currencies across %d dates",
                len(all_currencies),
                max(len(dates) for dates in inst.date_index.values())
                if inst.date_index
                else 0,
            )
            for ccy in sorted(all_currencies):
                rate_count = len(inst.data[ccy])
                logger.debug("  %s: %d dates", ccy, rate_count)

        return inst

    def has_rate_exact(self, date: dt.date, currency: Currency) -> bool:
        if currency.is_base:
            return True
        return currency in self.data and date in self.data[currency]

    def get_rate(self, date: dt.date, currency: Currency) -> Decimal | None:
        """Return EUR per 1 unit of currency, or None if no fresh rate is available.

        Falls back to the nearest *previous* observation to absorb weekends/holidays,
        but only within _MAX_FX_LOOKBACK_DAYS. A gap wider than that window returns None
        rather than an over-stale rate -- the usual cause being a lookup that runs more
        than that many days past the table's last entry -- and the caller then reports
        it missing (the same fatal path as an absent rate). Never forward-fills: a past
        event is not priced at a future rate.
        """
        if currency.is_base:
            return Decimal("1")
        if currency not in self.data:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (currency not in table)",
                currency,
                date,
            )
            return None

        if date in self.data[currency]:
            rate = self.data[currency][date]
            logger.debug(
                "FX rate lookup: %s on %s = %s (exact match)", currency, date, rate
            )
            return rate

        # Fall back to the nearest previous observation, but only within the staleness
        # cap (see _MAX_FX_LOOKBACK_DAYS). Find the latest date <= the requested one.
        dates = self.date_index[currency]

        pos = bisect.bisect_right(dates, date)
        if pos == 0:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (no earlier date available)",
                currency,
                date,
            )
            return None

        fallback_date = dates[pos - 1]
        days_back = (date - fallback_date).days
        if days_back > _MAX_FX_LOOKBACK_DAYS:
            # Past the cap the rate is too stale to trust; report it missing so the
            # caller's "FX incomplete" gate fires instead of silently extrapolating.
            logger.warning(
                "FX rate for %s on %s: nearest prior rate is %d days old (%s), beyond "
                "the %d-day staleness cap; treating as missing.",
                currency,
                date,
                days_back,
                fallback_date,
                _MAX_FX_LOOKBACK_DAYS,
            )
            return None

        rate = self.data[currency][fallback_date]
        logger.info(
            "FX rate for %s on %s: using rate from %s (%d days earlier) = %s",
            currency,
            date,
            fallback_date,
            days_back,
            rate,
        )
        return rate
