from __future__ import annotations

import bisect
import csv
import datetime as dt
import logging
from collections import defaultdict
from decimal import Decimal, DivisionByZero
from pathlib import Path

from capitangains.conv import date_key, to_dec_strict

logger = logging.getLogger(__name__)

# Hard cap on how stale a fallback rate may be. The nearest prior observation is
# accepted only within this window (covers weekends/holidays); a gap wider than the cap
# makes get_rate return None so the rate is reported missing rather than silently
# extrapolated. The usual cause is a lookup that runs more than this many days past the
# table's last entry.
_MAX_FX_LOOKBACK_DAYS = 7


class FxTable:
    """Date-indexed FX table: (date, currency) -> EUR per 1 unit of currency.

    Accepted CSV schema (base currency is EUR):
      date,currency,rate   where rate = target_currency_units_per_EUR
    """

    def __init__(self) -> None:
        # Map: currency -> { date -> Decimal(eur_per_unit) }, plus sorted date list
        self.data: dict[str, dict[str, Decimal]] = defaultdict(dict)
        self.date_index: dict[str, list[str]] = {}

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
                d = date_key(row["date"])
                ccy = row["currency"].strip().upper()
                if not ccy:
                    raise ValueError(f"FX row missing currency for date {d}")
                if ccy == "EUR":
                    # Store identity explicitly for completeness
                    inst.data[ccy][d] = Decimal("1")
                    continue

                units_per_eur = to_dec_strict(row["rate"])  # e.g., 1 EUR = 1.91 AUD
                if units_per_eur <= 0:
                    raise ValueError(
                        f"Encountered non-positive FX rate {units_per_eur} for {ccy} "
                        f"on {d}"
                    )
                try:
                    eur_per_unit = Decimal("1") / units_per_eur
                except DivisionByZero as exc:  # defensive, though checked above
                    raise ValueError(f"Invalid zero FX rate for {ccy} on {d}") from exc

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
        d = date.isoformat()
        return c in self.data and d in self.data[c]

    def get_rate(self, date: dt.date, currency: str) -> Decimal | None:
        """Return EUR per 1 unit of currency, or None if no fresh rate is available.

        Falls back to the nearest *previous* observation to absorb weekends/holidays,
        but only within ``_MAX_FX_LOOKBACK_DAYS``. A gap wider than that window returns
        None rather than an over-stale rate -- the usual cause being a lookup that runs
        more than that many days past the table's last entry -- and the caller then
        reports it missing (the same fatal path as an absent rate). Never forward-fills:
        a past event is not priced at a future rate (cf. finding #2).
        """
        c = currency.upper()
        if c == "EUR":
            return Decimal("1")
        if c not in self.data:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (currency not in table)", c, date
            )
            return None

        d = date.isoformat()
        if d in self.data[c]:
            rate = self.data[c][d]
            logger.debug("FX rate lookup: %s on %s = %s (exact match)", c, date, rate)
            return rate

        # Fall back to the nearest previous observation, but only within the staleness
        # cap (see _MAX_FX_LOOKBACK_DAYS). Find the latest date <= d in the sorted list.
        dates = self.date_index[c]

        pos = bisect.bisect_right(dates, d)
        if pos == 0:
            logger.debug(
                "FX rate lookup: %s on %s: NOT FOUND (no earlier date available)",
                c,
                date,
            )
            return None

        fallback_date_str = dates[pos - 1]

        # Freshness must be provable. An unparseable key means we cannot bound the
        # staleness, so fail closed (report missing) rather than return an unverifiable
        # rate. Such keys only arise from a malformed FX CSV (see finding #12).
        try:
            fallback_date = dt.date.fromisoformat(fallback_date_str)
        except ValueError:
            logger.warning(
                "FX rate for %s on %s: nearest key %r is unparseable; treating as "
                "missing.",
                c,
                date,
                fallback_date_str,
            )
            return None

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

        rate = self.data[c][fallback_date_str]
        logger.info(
            "FX rate for %s on %s: using rate from %s (%d days earlier) = %s",
            c,
            date,
            fallback_date,
            days_back,
            rate,
        )
        return rate
