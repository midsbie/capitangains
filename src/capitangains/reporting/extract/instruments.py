"""Extract the symbol -> ISIN map from the IBKR 'Financial Instrument Information'.

The 'Security ID' column carries each instrument's ISIN, whose 2-letter prefix is the
standard country-of-issuance proxy (the same proxy dividends use via isin_country). The
capital-gains sheet uses it to pre-fill the Anexo J 'Pais da Fonte' field, so the filer
does not hand-enter a country per lot.

Pure enrichment: a row that is not stock-like, or carries no symbol or no plausible
ISIN, is skipped rather than rejected. A symbol with no usable row simply gets no
country: its Anexo J source-country cell is left blank for the filer to fill by hand
(distinct from an ISO with no Tabela X code, which renders the raw ISO and is warned),
never a guessed code.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from capitangains.model import IbkrModel

from ._common import ASSET_STOCK_LIKE
from .sections import SEC_INSTRUMENTS

logger = logging.getLogger(__name__)

# ISIN shape: 2-letter ISO country + 9 alphanumeric + 1 check digit. Only the country
# prefix (the sole field we read) is validated, so a mis-shaped tail still yields one.
_ISIN_MIN_LEN = 12


@dataclass(frozen=True)
class InstrumentRow:
    """One instrument's ticker paired with its ISIN (Security ID)."""

    symbol: str
    isin: str

    @property
    def country(self) -> str:
        """The ISIN's 2-letter country-of-issuance prefix."""
        return self.isin[:2].upper()


def parse_instruments(model: IbkrModel) -> list[InstrumentRow]:
    """Extract (symbol, ISIN) pairs from the Financial Instrument Information section.

    Keeps only stock-like rows carrying a symbol and an ISIN whose 2-letter prefix is
    alphabetic; everything else is skipped (logged once at DEBUG). The result is a flat
    list; the caller folds it into a symbol -> country map.
    """
    out: list[InstrumentRow] = []
    skipped = 0
    for r in model.get_subtables(SEC_INSTRUMENTS):
        for row in r.rows:
            if row.get("Asset Category", "").strip() not in ASSET_STOCK_LIKE:
                skipped += 1
                continue
            symbol = row.get("Symbol", "").strip()
            isin = row.get("Security ID", "").strip()
            if not symbol or len(isin) < _ISIN_MIN_LEN or not isin[:2].isalpha():
                skipped += 1
                continue
            out.append(InstrumentRow(symbol=symbol, isin=isin))

    if skipped:
        logger.debug(
            "Financial Instrument Information: skipped %d non-stock/incomplete row(s)",
            skipped,
        )
    return out


def instrument_country_map(rows: Sequence[InstrumentRow]) -> dict[str, str]:
    """Fold instrument rows into a symbol -> ISIN-country map.

    A symbol appearing across several statements (one row per file) keeps a single
    country; the ISIN is stable, so a later row's country simply reaffirms the earlier.
    """
    return {row.symbol: row.country for row in rows}
