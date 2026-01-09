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

# Maximum number of days to look back for FX rate before warning
_MAX_FX_LOOKBACK_DAYS = 7


class FxTable:
    """Date-indexed FX table: (date, currency) -> EUR per 1 unit of currency.

    Accepted CSV schemas (base currency is EUR):
      - date,currency,rate            # rate = target_currency_units_per_EUR
      - date,currency,eur_per_unit    # geur_per_unit = EUR per 1 unit of currency
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
        """Return EUR per 1 unit of currency.

        If the exact date isn't available, falls back to the nearest previous
        available date for that currency (to accommodate weekends/holidays).
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

        # fallback to nearest previous date (weekends/holidays)
        # Find the latest date <= d in sorted list
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
        rate = self.data[c][fallback_date_str]
        # Best-effort logging of fallback distance; do not crash on malformed keys.
        try:
            fallback_date = dt.date.fromisoformat(fallback_date_str)
            days_back = (date - fallback_date).days
            if days_back > _MAX_FX_LOOKBACK_DAYS:
                logger.warning(
                    "FX rate for %s on %s using %d-day-old rate from %s. "
                    "Consider providing more recent FX data.",
                    c,
                    date,
                    days_back,
                    fallback_date,
                )
            else:
                logger.debug(
                    "FX rate lookup: %s on %s: fallback to %s (%d days earlier) = %s",
                    c,
                    date,
                    fallback_date,
                    days_back,
                    rate,
                )
        except ValueError:
            logger.debug(
                "FX rate lookup: %s on %s: fallback to %r (unparseable date key) = %s",
                c,
                date,
                fallback_date_str,
                rate,
            )

        return rate
