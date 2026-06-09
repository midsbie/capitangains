from __future__ import annotations

import bisect
import csv
import datetime as dt
import logging
from collections import defaultdict
from decimal import Decimal, DivisionByZero
from pathlib import Path

from capitangains.conv import parse_date, to_dec_strict

logger = logging.getLogger(__name__)

# Hard cap on how stale a fallback rate may be. The nearest prior observation is
# accepted only within this window (covers weekends/holidays); a gap wider than the cap
# makes get_rate return None so the rate is reported missing rather than silently
# extrapolated. The usual cause is a lookup that runs more than this many days past the
# table's last entry.
_MAX_FX_LOOKBACK_DAYS = 7


def _parse_fx_rate(raw: str | None, ccy: str, date: dt.date) -> Decimal:
    """Parse one operator-supplied FX rate strictly, rejecting an ambiguous comma.

    The --fx-table CSV declares no locale, so a comma in the rate is irrecoverably
    ambiguous: "1,0850" could be 10850 (thousands separator) or 1.0850 (decimal comma),
    readings that differ by ~10000x. Magnitude cannot disambiguate, since a rate may
    legitimately exceed 1000 (e.g. ~17000 IDR per EUR). The rate multiplies every
    foreign figure and a wrong-but-positive value trips no later guard, so guessing
    would silently misprice the whole report. A rate is always expressible without a
    comma, so reject one outright rather than pick an interpretation (re-interpreting it
    as a decimal comma would just move the silent error onto US-formatted input). With
    the comma excluded, to_dec_strict handles the rest (sign, decimal point, and the
    missing/placeholder/malformed cases). This is why operator rates do not go through
    the IBKR-grammar cleaner; see conv.NUM_CLEAN_RE.
    """
    # Guard the comma test on an actual string: a short CSV row leaves row["rate"] as
    # None, which would make `"," in raw` raise a raw TypeError. The None, empty, and
    # malformed cases are what to_dec_strict reports as a clean domain ValueError, so
    # let them fall through to it.
    if isinstance(raw, str) and "," in raw:
        raise ValueError(
            f"FX rate {raw!r} for {ccy} on {date} contains a comma; the FX table must "
            f"use a plain decimal point (a comma is ambiguous between a thousands "
            f"separator and a decimal comma)."
        )
    return to_dec_strict(raw)


class FxTable:
    """Date-indexed FX table: (date, currency) -> EUR per 1 unit of currency.

    Accepted CSV schema (base currency is EUR):
      date,currency,rate   where rate = target_currency_units_per_EUR
    """

    def __init__(self) -> None:
        # Map: currency -> { date -> Decimal(eur_per_unit) }, plus sorted date list.
        # Dates are real `d.date keys, not strings: comparison and bisect are then
        # chronological by construction, immune to the lexical-vs-chronological hazard a
        # non-canonical string key would introduce.
        self.data: dict[str, dict[dt.date, Decimal]] = defaultdict(dict)
        self.date_index: dict[str, list[dt.date]] = {}

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
                    d = parse_date(raw_date)
                except (ValueError, TypeError) as exc:
                    # Reject a non-canonical or malformed date loudly here rather than
                    # store it as an untrusted string and mis-sort/mis-match it later.
                    # A valid ISO date is a precondition for the table.
                    raise ValueError(
                        f"FX table has an unparseable date {raw_date!r}"
                    ) from exc
                ccy = row["currency"].strip().upper()
                if not ccy:
                    raise ValueError(f"FX row missing currency for date {d}")
                if ccy == "EUR":
                    # Store identity explicitly for completeness.
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

                # Any duplicate (currency, date) row means the upstream process emitted
                # the same key twice. A process that double-emits cannot be trusted to
                # have faithfully represented the rest of the table and, as a
                # consequence, we reject outright, like the loader's other per-row
                # validations.
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

    def has_rate_exact(self, date: dt.date, currency: str) -> bool:
        c = currency.upper()
        if c == "EUR":
            return True
        return c in self.data and date in self.data[c]

    def get_rate(self, date: dt.date, currency: str) -> Decimal | None:
        """Return EUR per 1 unit of currency, or None if no fresh rate is available.

        Falls back to the nearest *previous* observation to absorb weekends/holidays,
        but only within _MAX_FX_LOOKBACK_DAYS. A gap wider than that window returns None
        rather than an over-stale rate -- the usual cause being a lookup that runs more
        than that many days past the table's last entry -- and the caller then reports
        it missing (the same fatal path as an absent rate). Never forward-fills: a past
        event is not priced at a future rate.
        """
        c = currency.upper()
        if c == "EUR":
            return Decimal("1")
        if c not in self.data:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (currency not in table)", c, date
            )
            return None

        if date in self.data[c]:
            rate = self.data[c][date]
            logger.debug("FX rate lookup: %s on %s = %s (exact match)", c, date, rate)
            return rate

        # Fall back to the nearest previous observation, but only within the staleness
        # cap (see _MAX_FX_LOOKBACK_DAYS). Find the latest date <= the requested one.
        dates = self.date_index[c]

        pos = bisect.bisect_right(dates, date)
        if pos == 0:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (no earlier date available)",
                c,
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
                c,
                date,
                days_back,
                fallback_date,
                _MAX_FX_LOOKBACK_DAYS,
            )
            return None

        rate = self.data[c][fallback_date]
        logger.info(
            "FX rate for %s on %s: using rate from %s (%d days earlier) = %s",
            c,
            date,
            fallback_date,
            days_back,
            rate,
        )
        return rate
