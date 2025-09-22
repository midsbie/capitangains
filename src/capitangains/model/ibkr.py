from __future__ import annotations

import csv
import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

RowDict = dict[str, str]


@dataclass(frozen=True)
class Subtable:
    """A single subtable inside a section (same header; many rows)."""

    header: tuple[str, ...]
    rows: tuple[RowDict, ...]


@dataclass
class IbkrModel:
    """
    In-memory representation of an IBKR Activity Statement CSV (not Flex Query).

    sections[section_name] -> list of subtables
    """

    sections: dict[str, list[Subtable]] = field(default_factory=dict)

    def get_subtables(self, section_name: str) -> list[Mapping[str, Any]]:
        """Return the subtables for a section (as list of Subtable)."""
        return self.sections.get(section_name, [])

    def iter_rows(self, section_name: str) -> Iterable[RowDict]:
        """Iterate row dicts across all subtables for a section."""
        for sub in self.get_subtables(section_name):
            yield from sub.rows


@dataclass(frozen=True)
class ParseIssue:
    line_no: int
    severity: Literal["warning", "error"]
    message: str
    row_preview: Sequence[str] | None = None


@dataclass
class ParseReport:
    """Non-fatal diagnostics collected during parsing."""

    issues: list[ParseIssue] = field(default_factory=list)

    def warn(self, line_no: int, msg: str, row: Sequence[str] | None = None) -> None:
        self.issues.append(ParseIssue(line_no, "warning", msg, row))

    def error(self, line_no: int, msg: str, row: Sequence[str] | None = None) -> None:
        self.issues.append(ParseIssue(line_no, "error", msg, row))

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def log_with(self, log: logging.Logger) -> None:
        for i in self.issues:
            prefix = "ERROR" if i.severity == "error" else "WARN"
            if i.row_preview is not None:
                log.warning(
                    "%s: line %d: %s | row=%s",
                    prefix,
                    i.line_no,
                    i.message,
                    i.row_preview,
                )
            else:
                log.warning("%s: line %d: %s", prefix, i.line_no, i.message)


class IbkrStatementCsvParser:
    """
    SRP parser: maps raw CSV rows -> IbkrModel domain model (+ ParseReport).

    CSV shape (observed):
        row[0] = section name (e.g., "Trades", "Dividends", ...)
        row[1] = kind ("Header" | "Data" | other)
        row[2:] = header fields (when kind=Header) or data fields (when kind=Data)

    Each time a "Header" appears for a section, a new subtable starts. Subsequent
    "Data" rows map to the *current* subtable for that section until the next header.
    """

    def parse_file(
        self, path: str | Path, *, encoding: str = "utf-8", newline: str = ""
    ) -> tuple[IbkrModel, ParseReport]:
        with open(
            path, "r", encoding=encoding, errors="replace", newline=newline
        ) as fp:
            reader = csv.reader(fp)
            return self.parse_rows(reader)

    def parse_rows(
        self, rows: Iterable[Sequence[str]]
    ) -> tuple[IbkrModel, ParseReport]:
        report = ParseReport()
        sections_acc: dict[str, list[_MutableSubtable]] = {}

        current_section: str | None = None
        current_subtable: _MutableSubtable | None = None
        line_no = 0

        for row in rows:
            line_no += 1

            if not row:
                report.warn(line_no, "Empty row; skipped.")
                continue

            # Defensive: need at least 2 cols for 'section' and 'kind'
            if len(row) < 2:
                report.warn(
                    line_no, "Malformed or blank-ish row (< 2 cells); skipped.", row
                )
                continue

            # Strip BOM on first cell if present
            section = (row[0] or "").lstrip("\ufeff")
            kind = (row[1] or "").strip()
            payload = list(row[2:])  # copy

            if kind == "Header":
                # Start a new subtable with this header under the given section
                header = tuple(payload)
                if not header:
                    report.warn(
                        line_no,
                        "Header row with empty header; subtable created anyway.",
                        row,
                    )

                current_section = section
                current_subtable = _MutableSubtable(header=header)
                sections_acc.setdefault(current_section, []).append(current_subtable)
                continue

            if kind != "Data":
                report.warn(line_no, f"Unknown kind '{kind}'; row skipped.", row)
                continue

            # "Data" row
            if current_section is None or current_subtable is None:
                report.warn(
                    line_no, "Data row encountered before any header; row skipped.", row
                )
                continue

            # Map payload to header, with pad/trim to match header length.
            mapped = _map_row_to_header(payload, current_subtable.header)
            current_subtable.rows.append(mapped)

        # Freeze into the public immutable dataclasses
        model = IbkrModel(
            sections={
                sec: [sub.freeze() for sub in subtables]
                for sec, subtables in sections_acc.items()
            }
        )
        return model, report


@dataclass
class _MutableSubtable:
    header: tuple[str, ...]
    rows: list[RowDict] = field(default_factory=list)

    def freeze(self) -> Subtable:
        return Subtable(header=self.header, rows=tuple(self.rows))


def _map_row_to_header(data_vals: Sequence[str], header: Sequence[str]) -> RowDict:
    """Pad/trim data to header length and zip to a row dict."""
    hlen = len(header)
    if len(data_vals) < hlen:
        vals = list(data_vals) + [""] * (hlen - len(data_vals))
    else:
        vals = list(data_vals[:hlen])
    return dict(zip(header, vals))


def merge_models(models: Sequence[IbkrModel]) -> IbkrModel:
    """Merge multiple IbkrModel instances into one by concatenating subtables per section.

    Order is preserved by input sequence, then by original subtable order. No de-duplication.
    """
    sections: dict[str, list[Subtable]] = {}
    for m in models:
        for sec, subs in m.sections.items():
            sections.setdefault(sec, []).extend(subs)
    return IbkrModel(sections=sections)


def merge_reports(reports: Sequence[ParseReport]) -> ParseReport:
    out = ParseReport()
    for r in reports:
        out.issues.extend(r.issues)
    return out
