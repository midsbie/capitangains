"""Shared extraction helpers and the row-level defect record.

Package-internal: the outside world imports the public ExtractionDefect via the extract
package root, not from here.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from capitangains.conv import ELISION_PLACEHOLDERS, Currency, parse_date, to_dec_strict
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel


def _is_total_or_empty(value: str) -> bool:
    """Return True if value is empty or a 'Total' summary row."""
    return not value or value.lower().startswith("total")


def _require_fields(label: str, **fields: str) -> None:
    """Raise DataQualityError naming every empty required field."""
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise DataQualityError(f"Invalid {label}: missing {', '.join(missing)}")


def _require_decimal(label: str, field: str, value: str) -> Decimal:
    """Strictly parse a required numeric field, reporting failures as data errors.

    Wraps to_dec_strict so a missing/placeholder/malformed value surfaces as a
    structured DataQualityError (exit 2 at the CLI boundary) instead of an uncaught
    ValueError traceback. The original cause text is preserved.
    """
    try:
        return to_dec_strict(value)
    except ValueError as e:
        raise DataQualityError(f"Invalid {label}: bad {field} {value!r} ({e})") from e


def _optional_decimal(label: str, field: str, value: str | None) -> Decimal | None:
    """Parse an optional numeric field, yielding None only for a genuine elision.

    Basis and Realized P/L are the only optional columns of the IBKR Trades section:
    IBKR leaves them blank on Forex conversion rows (an FX leg has no cost basis or
    realized P/L). None is therefore a meaningful signal: gap synthesis reads it as "no
    basis to synthesize from" (and refuses to fabricate a cost), and reconciliation
    excludes the trade. So an absent cell (None or empty) or a known elision placeholder
    maps to None.

    A genuinely malformed cell, by contrast, is corruption (a column-shifted 'C;P', a
    typo'd '19,8X7.919') and must not be relabelled as elision, or it would enter those
    same paths as if IBKR had reported nothing. It surfaces as a DataQualityError (exit
    2 at the boundary), like _require_decimal, rather than being swallowed to None.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped in ELISION_PLACEHOLDERS:
        return None
    return _require_decimal(label, field, value)


def _require_date(label: str, field: str, value: str) -> dt.date:
    """Parse a required date field, reporting failures as data errors (see above)."""
    try:
        return parse_date(value)
    except ValueError as e:
        raise DataQualityError(f"Invalid {label}: bad {field} {value!r} ({e})") from e


@dataclass(frozen=True)
class ExtractionDefect:
    """One rejected extraction row, accumulated for a single boundary report.

    The extractors collect these instead of raising on the first bad row, so the
    operator sees every defect in one pass (the CLI logs each, then exits 2 -- mirroring
    the FX and gap-acknowledgment reports). Identity is the semantic locator (section,
    symbol, date), read from the row dict at the catch site so it survives even when one
    of those fields is itself the malformed value. reason is the DataQualityError text,
    which already names the first offending field and its bad value; the catch is
    per-row, so no separate field locator is needed.
    """

    section: str
    symbol: str | None
    date: str | None
    reason: str


_CashFlowRowT = TypeVar("_CashFlowRowT")


@dataclass(frozen=True)
class CashFlowFields:
    """The common columns of an IBKR cash-flow line, pre-stripped, plus the raw row.

    Date and Amount stay as raw strings: each builder parses them via the _require_*
    helpers (so a failure names the field) and controls the parse order that decides
    which defect is reported first. currency is already a normalized Currency (the field
    gate has run, so it is non-empty). raw is the escape hatch for section-specific
    columns (e.g. withholding's 'Code').
    """

    currency: Currency
    date_s: str
    description: str
    amount_s: str
    raw: dict[str, str]


def _extract_cashflow_section(
    model: IbkrModel,
    *,
    section: str,
    logger: logging.Logger,
    build: Callable[[CashFlowFields], _CashFlowRowT],
) -> tuple[list[_CashFlowRowT], list[ExtractionDefect]]:
    """Drive the shared flat cash-flow extraction for dividends/interest/withholding.

    Two dispositions. The only date-less rows IBKR emits are non-data trailers: a
    total/subtotal labelled in the Currency cell ('Total', 'Total in EUR'), and the
    fully blank separator row between subtables. Being expected, redundant aggregates,
    they are dropped under one DEBUG line, never fully silent yet adding no
    default-level noise. Every other row is a transaction and must carry all of Date,
    Currency and Description (Date sets the tax year, FX rate and FIFO order; Currency
    selects the rate; Description identifies the flow). A blank in any of them is
    surfaced as a defect (exit 2 at the boundary), not tolerated; a date-less row that
    is neither a labelled total nor fully blank (an amount that lost its Date and
    Currency, say) is corruption or an unrecognized format that breaks our assumptions,
    so it fails closed rather than being skipped. build owns section-specific parsing
    (Date format, Amount); it and the shared field gate are the only things that may
    raise, both caught here per-row as an ExtractionDefect.

    section keys iter_rows, the defect locator, and the DEBUG summary prefix.
    """
    out: list[_CashFlowRowT] = []
    defects: list[ExtractionDefect] = []
    skipped_totals = 0
    for r in model.iter_rows(section):
        cur = r.get("Currency", "").strip()
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amount_s = r.get("Amount", "").strip()

        # A non-data trailer is the only date-less row IBKR emits: a Total/subtotal
        # labelled in the Currency cell, or the fully blank separator between subtables.
        # A date-less row carrying any other content (an amount that lost its Date and
        # Currency) is not a trailer; it falls through to the field gate as a defect.
        is_trailer = cur.lower().startswith("total") or not (cur or desc or amount_s)
        if not date_s and is_trailer:
            skipped_totals += 1
            continue

        try:
            _require_fields(
                f"{section} row", Date=date_s, Currency=cur, Description=desc
            )
            out.append(build(CashFlowFields(Currency(cur), date_s, desc, amount_s, r)))
        except DataQualityError as e:
            defects.append(ExtractionDefect(section, None, date_s or None, str(e)))

    if skipped_totals:
        logger.debug("%s: skipped %d summary/total row(s)", section, skipped_totals)

    return out, defects
