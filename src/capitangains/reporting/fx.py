from __future__ import annotations

import bisect
import csv
import datetime as dt
from collections import defaultdict
from decimal import Decimal, DivisionByZero
from pathlib import Path

from capitangains.conv import date_key, to_dec_strict


class FxTable:
    """Date-indexed FX table: (date, currency) -> EUR per 1 unit of currency.

    Accepted CSV schemas (base currency is EUR):
      - date,currency,rate            # rate = target_currency_units_per_EUR
      - date,currency,eur_per_unit    # geur_per_unit = EUR per 1 unit of currency
    """

    def __init__(self):
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
            return None
        d = date.isoformat()
        if d in self.data[c]:
            return self.data[c][d]
        # fallback to nearest previous date (weekends/holidays)
        # Find the latest date <= d in sorted list
        dates = self.date_index[c]

        pos = bisect.bisect_right(dates, d)
        if pos == 0:
            return None
        return self.data[c][dates[pos - 1]]
