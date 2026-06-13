"""Group already-EUR-converted foreign income into Anexo J Quadro 8A lines.

Quadro 8A ("Rendimentos de Capitais") wants one row per (income code, source country)
carrying gross income and foreign tax in EUR. This module is the pure domain transform
that folds the per-payment dividend, interest, and withholding rows into those grouped
lines; it does no I/O and no FX (the rows arrive with ``amount_eur`` already populated).

It imports only the extract row dataclasses and the money quantizer, so it sits below
the builder and sink with no import cycle (extract depends on neither).
"""

from __future__ import annotations

import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .extract import DividendRow, InterestRow, WithholdingRow
from .money import quantize_money


class IncomeKind(enum.Enum):
    """An income stream's identity, carrying the two tax-form facts that travel with it:
    the Anexo J income code and the i18n key for its display label. Keeping both on the
    member (rather than in parallel side maps) means a new kind cannot be added with a
    missing code or label. PIL shares the dividend code E11 yet stays a distinct kind so
    its gross and tax keep their own line.
    """

    code: str
    label_key: str

    DIVIDEND = ("E11", "kind_dividend")
    PIL = ("E11", "kind_pil")  # payment in lieu of dividend
    INTEREST = ("E21", "kind_interest")

    def __init__(self, code: str, label_key: str) -> None:
        self.code = code
        self.label_key = label_key


# The phrase IBKR uses for a payment in lieu, keyed on identically for the gross side
# (dividend description) and the tax side (withholding description) so they route to the
# same line. Same phrase the withholding extractor classifies on.
_PIL_PHRASE = "payment in lieu"

# IBKR labels the margin/financing interest the account *pays* as "... Debit Interest
# ...". It is a financing cost, not Anexo J Box 8A capital income, so it is kept out of
# the interest gross; credit interest (and its reversals) and the SYEP summaries remain.
_DEBIT_INTEREST_PHRASE = "debit interest"

# A full ISIN embedded in a dividend description, e.g. "SLIGR(NL0000817179) Cash
# Dividend ...": 2 alpha country + 9 alnum + 1 check digit, anchored on the parentheses
# so a stray letter run (e.g. "EUR") cannot masquerade as a country code.
_ISIN_RE = re.compile(r"\(([A-Z]{2})[A-Z0-9]{9}[0-9]\)")


def isin_country(description: str) -> str | None:
    """The 2-letter ISIN country prefix embedded in a description, or None if absent.

    None lets the caller group as unknown-country rather than fabricate one.
    """
    m = _ISIN_RE.search(description)
    return m.group(1) if m else None


@dataclass(frozen=True)
class Quadro8ALine:
    """One Quadro 8A row: a single (income kind, source country) group's EUR totals."""

    kind: IncomeKind  # DIVIDEND / PIL / INTEREST (carries its form code + label key)
    country: str  # 2-letter source country; "" when an ISIN is missing/garbled
    gross_eur: Decimal  # summed EUR income for the group
    tax_eur: Decimal  # summed EUR foreign tax (positive; a refund nets against it)

    @property
    def income_code(self) -> str:
        """Anexo J income code: E11 for dividends and PIL, E21 for interest."""
        return self.kind.code


@dataclass
class _Group:
    """Mutable accumulator for one (kind, country) key while folding the rows."""

    gross: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")


def _dividend_kind(description: str) -> IncomeKind:
    return IncomeKind.PIL if _PIL_PHRASE in description.lower() else IncomeKind.DIVIDEND


def _is_income_interest(description: str) -> bool:
    """Whether an Interest row is Box 8A income (i.e. not debit/margin interest)."""
    return _DEBIT_INTEREST_PHRASE not in description.lower()


def _route_withholding(
    wh: WithholdingRow, broker_country: str
) -> tuple[IncomeKind, str] | None:
    """Map a withholding row to the (kind, country) of the gross group it offsets.

    Returns None for rows that carry no Anexo J income code (type "Unknown", already
    warned at extraction).
    """
    if wh.type == "Interest":
        # Interest withholding carries no country suffix in IBKR data, so it joins the
        # broker group. ``wh.country or`` is defensive: a suffixed one (should it ever
        # appear) routes to its own country instead.
        return IncomeKind.INTEREST, (wh.country or broker_country)
    if wh.type == "Dividend":
        # Country comes from the withholding's " - XX Tax" suffix (wh.country), which in
        # IBKR data equals the dividend's ISIN country, so the gross and its tax land on
        # one line. A PIL withholding routes to the PIL line even though both print E11.
        return _dividend_kind(wh.description), wh.country
    return None


def aggregate_quadro_8a(
    *,
    dividends: Sequence[DividendRow],
    interest: Sequence[InterestRow],
    withholding: Sequence[WithholdingRow],
    broker_country: str,
) -> list[Quadro8ALine]:
    """Fold per-payment income into Quadro 8A lines, one per (kind, country).

    Each side is grouped independently and then merged by key; there is never a per-row
    date/ISIN join between gross and tax. ``broker_country`` is the source country for
    broker-paid interest (which carries no country in the data). A group may have gross
    with zero tax (e.g. UK 0% dividend WHT) or tax with zero gross, and both still
    materialize. Lines come back sorted by (income code, kind, country).

    Amounts arrive already converted to EUR; a None ``amount_eur`` (an unpriced row) is
    skipped. The live pipeline never reaches here with one, since a missing FX rate
    aborts the run before the report is built, but the guard keeps this pure transform
    total for any caller.
    """
    groups: dict[tuple[IncomeKind, str], _Group] = {}

    def group_for(kind: IncomeKind, country: str) -> _Group:
        return groups.setdefault((kind, country), _Group())

    # Gross: dividends and payments in lieu, keyed by their ISIN source country.
    for div in dividends:
        if div.amount_eur is not None:
            group_for(
                _dividend_kind(div.description), isin_country(div.description) or ""
            ).gross += div.amount_eur

    # Gross: broker-paid interest (credit + monthly SYEP summaries) under one injected
    # jurisdiction. Debit (margin) interest is a financing cost, not Box 8A income, so
    # _is_income_interest drops it. The per-loan syep_interest detail is deliberately
    # not a parameter, preventing a double-count with the SYEP lines interest carries.
    for intr in interest:
        if intr.amount_eur is not None and _is_income_interest(intr.description):
            group_for(IncomeKind.INTEREST, broker_country).gross += intr.amount_eur

    # Tax: foreign withholding routed onto the matching gross group. amount_eur is
    # stored negative, so accumulate signed and negate once at the end; a positive
    # reversal then nets against the tax instead of inflating it.
    for wh in withholding:
        routed = _route_withholding(wh, broker_country)
        if routed is not None and wh.amount_eur is not None:
            group_for(*routed).tax += wh.amount_eur

    lines = [
        Quadro8ALine(
            kind=kind,
            country=country,
            gross_eur=quantize_money(group.gross),
            tax_eur=quantize_money(-group.tax),
        )
        for (kind, country), group in groups.items()
    ]
    return sorted(
        lines, key=lambda line: (line.kind.code, line.kind.name, line.country)
    )
