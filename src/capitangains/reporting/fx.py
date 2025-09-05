from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

from capitangains.core import date_key, to_dec


class FxTable:
    """Simple date-indexed FX table: (date, currency) -> eur_per_unit.

    CSV format required:
        date,currency,eur_per_unit
        2024-01-02,USD,0.9123
        2024-01-02,GBP,1.1620
    """

    def __init__(self):
        # Map: currency -> { date -> Decimal(eur_per_unit) }, plus sorted date list
        self.data: dict[str, dict[str, Decimal]] = defaultdict(dict)
        self.date_index: dict[str, list[str]] = {}

    @classmethod
    def from_csv(cls, path: Union[str, Path]) -> "FxTable":
        inst = cls()
        with open(path, "r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            required = {"date", "currency", "eur_per_unit"}
            missing = required - set((reader.fieldnames or []))
            if missing:
                raise ValueError(f"FX table missing columns: {missing}")
            for row in reader:
                d = date_key(row["date"])
                ccy = row["currency"].strip().upper()
                rate = to_dec(row["eur_per_unit"])
                inst.data[ccy][d] = rate
        for ccy, m in inst.data.items():
            inst.date_index[ccy] = sorted(m.keys())
        return inst

    def get_rate(self, date: dt.date, currency: str) -> Optional[Decimal]:
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
        # binary search
        import bisect

        pos = bisect.bisect_right(dates, d)
        if pos == 0:
            return None
        return self.data[c][dates[pos - 1]]

