"""Statement-level metadata: the account and reporting period of one IBKR statement.

Unlike the row extractors, which yield a list of typed rows, this interprets the
singular identity of the statement as a whole. StatementPeriod and StatementMetadata are
pure value objects (valid by construction); parse_statement_metadata is the extractor
that reads them out of the raw model.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

# IBKR renders the Statement 'Period' field with a full month name, e.g.
# "January 1, 2024" -- distinct from the ISO Date/Time carried on trade rows.
_PERIOD_DATE_FORMAT = "%B %d, %Y"
_PERIOD_SEPARATOR = " - "


@dataclass(frozen=True)
class StatementPeriod:
    """A closed [start, end] reporting interval, valid by construction.

    The start <= end invariant is enforced here rather than at any single parse
    site, so no code path -- whether building a period from the CSV or directly from two
    dates -- can ever hold an inverted interval. Overlap is a method on the type so the
    closed-interval test has one authoritative home instead of being re-derived by each
    caller.
    """

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"Statement period ends before it starts: {self.start} to {self.end}"
            )

    @classmethod
    def parse(cls, text: str) -> StatementPeriod:
        """Parse an IBKR Statement 'Period' field into a closed interval.

        'January 1, 2024 - December 31, 2024' yields start 2024-01-01, end 2024-12-31. A
        single-day statement carries no separator, so start == end. Raises ValueError on
        a malformed value (missing/extra separator, a side that is not a month-name
        date, or a reversed range).
        """
        parts = text.split(_PERIOD_SEPARATOR)
        if len(parts) == 1:
            start_str = end_str = parts[0].strip()
        elif len(parts) == 2:
            start_str, end_str = parts[0].strip(), parts[1].strip()
        else:
            raise ValueError(
                f"Ambiguous statement period (multiple separators): {text!r}"
            )

        try:
            start = dt.datetime.strptime(start_str, _PERIOD_DATE_FORMAT).date()
            end = dt.datetime.strptime(end_str, _PERIOD_DATE_FORMAT).date()
        except ValueError as e:
            raise ValueError(f"Unparseable statement period: {text!r}") from e

        return cls(start, end)

    def overlaps(self, other: StatementPeriod) -> bool:
        """True when the two closed intervals share at least one calendar day."""
        return self.start <= other.end and other.start <= self.end


@dataclass(frozen=True)
class StatementMetadata:
    """Identifying metadata of one IBKR Activity Statement: its account and period."""

    account: str
    period: StatementPeriod


def parse_statement_metadata(model: IbkrModel) -> StatementMetadata:
    """Extract the account number and reporting period from a parsed statement.

    Raises DataQualityError naming the first missing or malformed field. Unlike the row
    extractors -- which accumulate a defect list because they yield many rows -- this
    yields a single value, so it raises on the first problem and leaves accumulation to
    its one caller (the multi-file input-conflict gate, which catches per file).
    """
    account = _field_value(model, "Account Information", "Account")
    if not account:
        raise DataQualityError("missing account number (Account Information)")

    period_text = _field_value(model, "Statement", "Period")
    if not period_text:
        raise DataQualityError("missing reporting period (Statement)")

    try:
        period = StatementPeriod.parse(period_text)
    except ValueError as e:
        raise DataQualityError(str(e)) from e

    return StatementMetadata(account=account, period=period)


def _field_value(model: IbkrModel, section: str, field_name: str) -> str | None:
    """First non-empty 'Field Value' whose 'Field Name' matches in a metadata section.

    IBKR metadata sections ('Statement', 'Account Information') are flat Field
    Name/Field Value tables; this locates one field's value, stripped. Returns None when
    the field is absent; a present-but-blank field yields an empty string, which callers
    reject the same way as a missing one.
    """
    for row in model.iter_rows(section):
        if row.get("Field Name") == field_name:
            value = row.get("Field Value")
            return value.strip() if value else None
    return None
