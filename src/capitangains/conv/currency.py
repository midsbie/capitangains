from __future__ import annotations

from dataclasses import dataclass

# The report's base currency. EUR-denominated values are already in the base, so the FX
# layer resolves their rate to Decimal('1') and conversion is the identity. Defined
# before Currency so both is_base and the EUR singleton below can reference it.
_BASE_CODE = "EUR"


@dataclass(frozen=True, order=True, slots=True)
class Currency:
    """An ISO-style currency code, normalized to uppercase on construction.

    Currency is the canonical identity key for a denominated value: the FIFO lot book,
    the per-symbol aggregates, and the realized-P/L cross-check all key on it, so two
    codes that differ only in case or surrounding whitespace MUST collapse to one key.
    Holding that invariant in the type (an un-normalized Currency cannot exist) means
    the rest of the pipeline never re-normalizes. Parse a raw cell into a Currency at
    the edge (the extractors, the FX-table loader) and unwrap with str() only at a
    display or serialization sink.

    An unknown or malformed code is preserved, since the FX table and statements carry
    open-ended codes and this type is not the authority on which exist. Emptiness and
    shape stay gated by the extractors that build it.

    order=True gives a deterministic sort by code, so a report over a set of currencies
    (the missing-FX list, the per-currency summary rows) is stable with no sort key.
    """

    code: str

    def __post_init__(self) -> None:
        # frozen: assign through object.__setattr__. strip() absorbs incidental CSV
        # whitespace; upper() folds case so 'usd' and 'USD' are one key and one value.
        object.__setattr__(self, "code", self.code.strip().upper())

    @property
    def is_base(self) -> bool:
        """Whether this is the base currency (EUR), which needs no FX conversion."""
        return self.code == _BASE_CODE

    def __str__(self) -> str:
        return self.code


# The base-currency singleton; prefer it over constructing Currency("EUR") ad hoc.
EUR = Currency(_BASE_CODE)
