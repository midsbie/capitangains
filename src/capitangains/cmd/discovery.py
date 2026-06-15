"""Auto-discovery of IBKR statements in a directory, selected by reporting period.

Pure: given a directory and the reporting year, classify every *.csv into the set to
feed the pipeline (this year's statements and the prior years FIFO needs for opening
lots), the future-dated set excluded from this run, and the non-statement files ignored.
No printing, logging, or process exit -- the boundary (cmd.cli) renders the manifest and
decides what to do, and the pipeline owns the fatal path for any statement whose
identity it cannot read.

Deliberately does NOT reuse reporting.source.ParsedStatement (that is extraction output,
the six section row-sets) nor share the pipeline's parse: a statement is parsed once
here to classify it and again in the pipeline to report on it. The double-parse is
accepted by design so RunOptions and run stay untouched -- discovery only resolves which
paths to feed them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from capitangains.errors import DataQualityError
from capitangains.model import IbkrStatementCsvParser
from capitangains.reporting.extract import StatementPeriod, parse_statement_metadata


@dataclass(frozen=True)
class DiscoveredStatement:
    """One IBKR-shaped CSV in the directory, with its reporting period if readable.

    period is None when the file carries a 'Statement' section (so it is an IBKR
    Activity Statement) but its identity could not be parsed. Such a file cannot be
    year-filtered, so discovery includes it in selected rather than dropping it
    silently; the pipeline's identity gate then reports it and halts.
    """

    path: Path
    period: StatementPeriod | None


@dataclass(frozen=True)
class DiscoveryResult:
    """The three buckets every directory *.csv falls into for one reporting year.

    selected feeds the pipeline (ordered by reporting period, then path, with any
    identity-unreadable file last); excluded_future is later than the reporting year and
    out of scope for this run; ignored is every csv that is not an IBKR statement (no
    'Statement' section).
    """

    selected: tuple[DiscoveredStatement, ...]
    excluded_future: tuple[DiscoveredStatement, ...]
    ignored: tuple[Path, ...]


def discover_statements(directory: Path, year: int) -> DiscoveryResult:
    """Classify a directory's *.csv into selected / future / ignored, by period.

    Walks the directory's .csv files (matched case-insensitively, non-recursively) in
    sorted path order and parses each once. For each:

    1. No 'Statement' section -> ignored. The presence of that section is the
       structural discriminator between an IBKR Activity Statement and an unrelated csv
       (e.g. a forex-rate table); a non-statement is silently skipped, not an error.
    2. Otherwise its identity is read. If it will not parse (a DataQualityError),
       the file is IBKR-shaped but cannot be year-filtered, so it is selected with
       period=None and never dropped -- the pipeline's identity gate reports it.
    3. Otherwise the period decides: a statement whose period starts in year or
       earlier is selected (this year's, plus prior years that seed FIFO); a later
       one is excluded_future.

    selected is sorted by (period start, path) with identity-unreadable entries last;
    the None period is kept out of the comparison rather than ordered against a
    date. excluded_future and ignored retain discovery (sorted path) order.
    """
    parser = IbkrStatementCsvParser()
    selected: list[DiscoveredStatement] = []
    excluded_future: list[DiscoveredStatement] = []
    ignored: list[Path] = []

    # Match the .csv suffix case-insensitively so a .CSV export is not silently dropped,
    # and uniformly across case-sensitive/insensitive filesystems (which
    # directory.glob("*.csv") is not). iterdir is non-recursive, like the prior glob.
    csv_paths = sorted(p for p in directory.iterdir() if p.suffix.casefold() == ".csv")
    for path in csv_paths:
        model, _ = parser.parse_file(path)
        if "Statement" not in model.sections:
            ignored.append(path)
            continue

        try:
            metadata = parse_statement_metadata(model)
        except DataQualityError:
            selected.append(DiscoveredStatement(path=path, period=None))
            continue

        if metadata.period.start.year <= year:
            selected.append(DiscoveredStatement(path=path, period=metadata.period))
        else:
            excluded_future.append(
                DiscoveredStatement(path=path, period=metadata.period)
            )

    # Two-pass sort: dated entries by (period start, path), identity-unreadable entries
    # last by path. Splitting on None keeps it out of the date comparison (deterministic
    # and mypy-clean) rather than ordering an absent period against a present one.
    dated: list[tuple[dt.date, str, DiscoveredStatement]] = []
    undated: list[DiscoveredStatement] = []
    for s in selected:
        if s.period is None:
            undated.append(s)
        else:
            dated.append((s.period.start, str(s.path), s))

    dated.sort(key=lambda keyed: (keyed[0], keyed[1]))
    undated.sort(key=lambda s: str(s.path))

    return DiscoveryResult(
        selected=tuple(s for _, _, s in dated) + tuple(undated),
        excluded_future=tuple(excluded_future),
        ignored=tuple(ignored),
    )
