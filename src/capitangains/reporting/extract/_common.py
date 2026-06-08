"""Shared extraction helpers and the row-level defect record.

Package-internal: the outside world imports the public ``ExtractionDefect`` via the
``extract`` package root, not from here.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from capitangains.conv import ELISION_PLACEHOLDERS, parse_date, to_dec_strict
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel


def _is_total_or_empty(value: str) -> bool:
    """Return True if value is empty or a 'Total' summary row."""
    return not value or value.lower().startswith("total")


def _is_data_row(currency: str, date: str, description: str) -> bool:
    """Return True if a cash-flow row carries the core fields of a data line.

    A row is a data line only when currency, date, and description are all present;
    rows failing this are typically Total/summary or otherwise non-data lines. Genuine
    structural anomalies are already surfaced by IbkrStatementCsvParser, so callers skip
    these quietly (aggregating a count) rather than re-logging per row.
    """
    return bool(currency and date and description)


def _require_fields(label: str, **fields: str) -> None:
    """Raise DataQualityError naming every empty required field."""
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise DataQualityError(f"Invalid {label}: missing {', '.join(missing)}")


def _require_decimal(label: str, field: str, value: str) -> Decimal:
    """Strictly parse a required numeric field, reporting failures as data errors.

    Wraps ``to_dec_strict`` so a missing/placeholder/malformed value surfaces as a
    structured ``DataQualityError`` (exit 2 at the CLI boundary) instead of an
    uncaught ``ValueError`` traceback. The original cause text is preserved.
    """
    try:
        return to_dec_strict(value)
    except ValueError as e:
        raise DataQualityError(f"Invalid {label}: bad {field} {value!r} ({e})") from e


def _optional_decimal(label: str, field: str, value: str | None) -> Decimal | None:
    """Parse an optional numeric field, yielding None only for a genuine elision.

    Basis and Realized P/L are optional IBKR columns where None is a meaningful signal:
    gap synthesis reads it as "no basis to synthesize from" (and refuses to fabricate a
    cost), and reconciliation excludes the trade. So an absent cell (None or empty) or
    a known elision placeholder maps to None.

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
    of those fields is itself the malformed value. ``reason`` is the DataQualityError
    text, which already names the first offending field and its bad value; the catch is
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
    which defect is reported first. ``raw`` is the escape hatch for section-specific
    columns (e.g. withholding's 'Code').
    """

    currency: str
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
    incomplete_label: str,
    incomplete_detail: str,
    skip_totals: bool = False,
) -> tuple[list[_CashFlowRowT], list[ExtractionDefect]]:
    """Drive the shared flat cash-flow extraction for dividends/interest/withholding.

    These sections share one shape: iterate the rows, drop summary/incomplete lines
    (counting the incomplete ones for a single boundary log), and build one typed row
    per data line, collecting any per-row DataQualityError as an ExtractionDefect rather
    than raising. ``build`` owns the section-specific construction and is the only thing
    that may raise; everything around it is this scaffolding.

    ``section`` keys both iter_rows and the defect locator. ``skip_totals`` silently
    drops 'Total'/empty-currency trailers before the data-row gate (interest only).
    ``incomplete_label``/``incomplete_detail`` word the one skipped-row summary, logged
    on the caller-supplied ``logger`` so it surfaces under that section's logger name.
    """
    out: list[_CashFlowRowT] = []
    defects: list[ExtractionDefect] = []
    skipped_incomplete = 0
    for r in model.iter_rows(section):
        cur = r.get("Currency", "").strip()
        if skip_totals and _is_total_or_empty(cur):
            continue
        date_s = r.get("Date", "").strip()
        desc = r.get("Description", "").strip()
        amount_s = r.get("Amount", "").strip()
        if not _is_data_row(cur, date_s, desc):
            skipped_incomplete += 1
            continue

        try:
            out.append(build(CashFlowFields(cur, date_s, desc, amount_s, r)))
        except DataQualityError as e:
            defects.append(ExtractionDefect(section, None, date_s or None, str(e)))

    if skipped_incomplete:
        logger.info(
            "%s: skipped %d incomplete row(s) (%s)",
            incomplete_label,
            skipped_incomplete,
            incomplete_detail,
        )
    return out, defects
