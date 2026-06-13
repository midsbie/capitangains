"""Boundary diagnostics for one report run.

These helpers translate the pipeline's computed results into user-facing log lines and,
where a precondition is violated, a process exit. They share one stance: accumulate
every problem of a kind, emit one ERROR per item plus a summary, then raise a single
SystemExit -- never fail-fast -- so the operator sees every defect in one pass.

The report_* helpers take already-computed findings (and a logger), never raw files or
argparse: pure detection lives in capitangains.reporting.validation and the per-stage
producers, and this module only renders the result and sets the exit.
capitangains.pipeline sequences detection and reporting between the stages whose output
they check.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence

from .conv import Currency, parse_date
from .errors import EXIT_DATA_QUALITY, DataQualityError
from .reporting import (
    ExtractionDefect,
    OrderingCollision,
    Quadro8ALine,
    ReconciliationReport,
    SymbolReconciliation,
    UnrecognizedSection,
)
from .reporting.fifo_domain import GapEvent, GapKey, GapResolution


def parse_acknowledged_gaps(spec: str | None) -> frozenset[GapKey]:
    """Parse the operator's itemized gap-acknowledgment spec into a set of keys.

    The spec is a comma-separated list of SYMBOL@YYYY-MM-DD tokens, each naming one
    unmatched SELL the operator has reviewed and authorized to be valued from IBKR's
    per-trade Basis. Symbols are case-sensitive and compared verbatim (only surrounding
    whitespace is stripped); a single key authorizes every gap sharing that (symbol,
    date). Empty tokens (from a leading, trailing, or doubled comma) are skipped; None
    or an all-empty spec yields an empty set; zero acknowledgments.  Every malformed
    token is collected and reported together as one DataQualityError so the spec can be
    fixed in a single pass.
    """
    if spec is None:
        return frozenset()

    keys: set[GapKey] = set()
    malformed: list[str] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        parts = token.split("@")
        if len(parts) != 2:
            malformed.append(token)
            continue
        symbol, date_str = parts[0].strip(), parts[1].strip()
        if not symbol or not date_str:
            malformed.append(token)
            continue
        try:
            key_date = parse_date(date_str)
        except ValueError:
            malformed.append(token)
            continue
        keys.add((symbol, key_date))

    if malformed:
        raise DataQualityError(
            "Malformed --auto-fix-sell-gaps acknowledgment(s) "
            f"(expected SYMBOL@YYYY-MM-DD): {', '.join(malformed)}"
        )
    return frozenset(keys)


def report_gap_acknowledgments(
    gaps: Sequence[GapEvent],
    acknowledged: frozenset[GapKey],
    logger: logging.Logger,
) -> None:
    """Tie out the gaps found against the operator's acknowledgments, at the boundary.

    A SELL gap is always a real, in-scope disposal that must be reported; the only
    open question is the valuation of its cost basis, and that valuation is never
    auto-applied without explicit, audited sign-off. So this reconciles two ways:

    - Every gap must be acknowledged: an UNACKNOWLEDGED gap is fatal.
    - Every acknowledged gap must have a usable IBKR Basis: a DEFECTIVE one (missing
      Basis, or a Basis its own Realized P/L contradicts) is fatal.
    - Every acknowledgment must match a gap found this run: an orphan acknowledgment
      (stale, mistyped, or for a gap that did not occur) is fatal.

    All three are accumulated into one pre-flight report: one ERROR per offending item,
    a summary ERROR, then a single SystemExit(EXIT_DATA_QUALITY) -- no fail-fast, so the
    operator sees every problem in one pass. Only when the tie-out is clean does each
    SYNTHESIZED gap emit its per-lot audit WARNING (the synthetic cost basis is never
    silent).
    """
    if not gaps and not acknowledged:
        return

    gap_keys = {(ge.symbol, ge.date) for ge in gaps}
    orphans = sorted(acknowledged - gap_keys)
    unacknowledged = [ge for ge in gaps if ge.outcome is GapResolution.UNACKNOWLEDGED]
    defective = [ge for ge in gaps if ge.outcome is GapResolution.DEFECTIVE]

    if unacknowledged or defective or orphans:
        for ge in unacknowledged:
            logger.error(
                "Unacknowledged SELL gap: symbol=%s date=%s qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        for ge in defective:
            logger.error(
                "Acknowledged SELL gap has a defective IBKR Basis: symbol=%s date=%s "
                "qty=%s currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )
        for sym, day in orphans:
            logger.error(
                "Orphan acknowledgment: %s@%s matched no SELL gap this run "
                "(stale, mistyped, or for a gap that did not occur).",
                sym,
                day,
            )
        logger.error(
            "Gap acknowledgment tie-out failed: %d unacknowledged gap(s), %d "
            "acknowledged gap(s) with a defective Basis, %d orphan acknowledgment(s). "
            "Acknowledge each reviewed gap explicitly with "
            '--auto-fix-sell-gaps "SYMBOL@YYYY-MM-DD[,...]" (only after verifying its '
            "IBKR Basis), and drop any acknowledgment that matches no gap.",
            len(unacknowledged),
            len(defective),
            len(orphans),
        )
        raise SystemExit(EXIT_DATA_QUALITY)

    for ge in gaps:
        if ge.outcome is GapResolution.SYNTHESIZED:
            logger.warning(
                "Synthesized residual lot for unmatched SELL -- basis taken from IBKR "
                "Basis, not independently verified: symbol=%s date=%s qty=%s "
                "currency=%s | %s",
                ge.symbol,
                ge.date,
                ge.remaining_qty,
                ge.currency,
                ge.message,
            )


def report_extraction_defects(
    defects: Sequence[ExtractionDefect], logger: logging.Logger
) -> None:
    """Abort if any extraction row was rejected, listing every defect first.

    Each extractor accumulates its row-level data-quality defects instead of raising on
    the first bad row, so the operator sees every problem in one pass (mirroring the FX
    and gap-acknowledgment reports above). One ERROR per defect, a summary ERROR, then a
    single SystemExit(EXIT_DATA_QUALITY) -- no workbook written.
    """
    if not defects:
        return

    for d in defects:
        logger.error(
            "Row rejected in %s (symbol=%s, date=%s): %s",
            d.section,
            d.symbol or "n/a",
            d.date or "n/a",
            d.reason,
        )

    logger.error(
        "Extraction rejected %d row(s); no workbook written. Correct the offending "
        "row(s) above and rerun.",
        len(defects),
    )
    raise SystemExit(EXIT_DATA_QUALITY)


def report_missing_fx(
    missing: set[tuple[dt.date, Currency]], logger: logging.Logger
) -> None:
    """Abort if the FX table could not supply every rate the EUR report needs.

    A complete table is a precondition: substituting another date's rate would silently
    misstate cost basis or proceeds, and blank EUR cells would understate totals. List
    every missing (date, currency) so the table can be completed in one pass, then exit
    2 without writing a workbook.
    """
    if not missing:
        return

    for d, ccy in sorted(missing):
        logger.error("Missing FX rate: %s on %s", ccy, d)

    logger.error(
        "EUR conversion incomplete: %d required FX rate(s) absent from the table; "
        "no workbook written. Add the rate(s) above (or widen the table's date range) "
        "and rerun.",
        len(missing),
    )
    raise SystemExit(EXIT_DATA_QUALITY)


def report_symbol_currency_violations(
    violations: Mapping[str, frozenset[Currency]], logger: logging.Logger
) -> None:
    """Abort if any symbol appears under more than one trade currency.

    The detector (reporting.validation.detect_symbol_currency_violations) owns the
    rationale; here we emit one ERROR per offending symbol plus a summary, then exit 2.
    An empty mapping means the invariant holds and nothing is logged.
    """
    if not violations:
        return

    for sym, ccys in sorted(violations.items()):
        logger.error(
            "Symbol %s maps to multiple trade currencies: %s",
            sym,
            ", ".join(str(c) for c in sorted(ccys)),
        )

    logger.error(
        "symbol-currency uniqueness violated -- %d symbol(s) map to more than one "
        "trade currency (each must map to exactly one); no workbook written.",
        len(violations),
    )
    raise SystemExit(EXIT_DATA_QUALITY)


def report_ordering_collisions(
    collisions: Sequence[OrderingCollision], logger: logging.Logger
) -> None:
    """Abort if any same-day event group's intraday FIFO order is undetermined.

    The detector (reporting.validation.detect_ordering_collisions) owns the
    why-unorderable rationale and the scope. The boundary policy lives here: rather than
    fabricate an order IBKR did not provide, whether for an untimed transfer or a
    date-only trade, and silently risk a wrong cost basis on exactly the figures this
    tool exists to get right, we refuse to guess. A tax figure must be correct rather
    than plausibly guessed (the stance already taken for a missing FX rate or an
    unmatched sell), so every collision is listed, then a single
    SystemExit(EXIT_DATA_QUALITY), and no workbook is written. These collisions are rare
    (settlement windows push transfers and same-symbol trading apart, and trade rows are
    normally timestamped), so no override mechanism is built; an empty list is silent.
    """
    if not collisions:
        return

    for c in collisions:
        logger.error(
            "Unorderable same-day events for %s (%s) on %s: %d trade(s) (%d without an "
            "intraday time) and %d transfer(s) share the date, and an event with no "
            "intraday time cannot be ordered for FIFO.",
            c.symbol,
            c.currency,
            c.date,
            c.n_trades,
            c.n_untimed_trades,
            c.n_transfers,
        )

    logger.error(
        "Same-day event ordering is ambiguous for %d symbol-day(s); no workbook "
        "written. IBKR does not timestamp transfers, and some trade rows carry a "
        "date-only Date/Time; either way an event landing on the same day as other "
        "activity in the same symbol cannot be ordered against it, and the cost basis "
        "would depend on an assumption the data does not support. Resolve the affected "
        "symbol(s) by hand, or adjust the inputs so the events do not coincide.",
        len(collisions),
    )
    raise SystemExit(EXIT_DATA_QUALITY)


def report_unrecognized_sections(
    findings: Sequence[UnrecognizedSection], logger: logging.Logger
) -> None:
    """Warn (do NOT abort) on sections no extractor consumed.

    Unlike the fail-closed reporters here, this never raises: a present-but-unconsumed
    section may be a renamed data-bearing section or a benign new one, and we cannot
    tell which, so we surface it rather than refuse the report (mirroring the parser's
    unknown-kind warning). One WARNING per section plus a summary; empty == silent.
    """
    if not findings:
        return
    for f in findings:
        logger.warning(
            "Unrecognized statement section %r (%d subtable(s), %d row(s)) is "
            "consumed by no extractor. IBKR may have renamed or added a section; "
            "verify it carries no taxable data this report must include.",
            f.name,
            f.subtable_count,
            f.row_count,
        )
    logger.warning(
        "%d statement section(s) present but consumed by no extractor. "
        "Not fatal, but if any carries taxable data, add an extractor or an "
        "allow-list entry.",
        len(findings),
    )


def report_unattributed_income(
    lines: Sequence[Quadro8ALine], logger: logging.Logger
) -> None:
    """Warn (do NOT abort) on Quadro 8A income with no identifiable source country.

    The detector (reporting.validation.detect_unattributed_income) owns the rationale.
    Unlike the fail-closed reporters here this never raises: the EUR gross and foreign
    tax are correct, only the source-country label is missing, so the report is still
    usable and the operator supplies the source country by hand on the form. One WARNING
    per line plus a summary; empty == silent.
    """
    if not lines:
        return
    for line in lines:
        logger.warning(
            "Quadro 8A income with no identifiable source country: kind=%s code=%s "
            "gross=%s EUR foreign_tax=%s EUR. The description carried no ISIN or "
            "'- XX Tax' suffix; supply the source country by hand on Anexo J.",
            line.kind.name,
            line.income_code,
            line.gross_eur,
            line.tax_eur,
        )
    logger.warning(
        "%d Quadro 8A line(s) carry income with no identifiable source country; not "
        "fatal, but supply each source country by hand before filing.",
        len(lines),
    )


def report_orphaned_foreign_tax(
    lines: Sequence[Quadro8ALine], logger: logging.Logger
) -> None:
    """Warn (do NOT abort) on Quadro 8A foreign tax with no matching gross income.

    The detector (reporting.validation.detect_orphaned_foreign_tax) owns the rationale.
    Like report_unattributed_income this never raises: the tax figure is correct, it
    just could not be attached to a gross income line (usually a dividend whose
    description carried no ISIN, so its gross landed under a separate empty-country
    line). The operator reconciles the two halves by hand on Anexo J. One WARNING per
    line plus a summary; empty == silent.
    """
    if not lines:
        return
    for line in lines:
        logger.warning(
            "Quadro 8A foreign tax with no matching gross income: kind=%s code=%s "
            "country=%s foreign_tax=%s EUR. The gross income could not be attributed "
            "to this (kind, country); reconcile it by hand on Anexo J.",
            line.kind.name,
            line.income_code,
            line.country,
            line.tax_eur,
        )
    logger.warning(
        "%d Quadro 8A line(s) carry foreign tax with no matching gross income; not "
        "fatal, but reconcile each against its income by hand before filing.",
        len(lines),
    )


def _list_then_abort(
    problems: Sequence[str], summary: str, logger: logging.Logger
) -> None:
    """List every problem string, then abort with the summary and no workbook.

    The shared tail of the string-list reporters below: one ERROR per already-formatted
    problem, a summary ERROR, then a single SystemExit(EXIT_DATA_QUALITY) -- never
    fail-fast. An empty sequence is silent. The caller passes ``summary`` already
    formatted, since one reporter interpolates a count and the other is fixed.
    """
    if not problems:
        return

    for problem in problems:
        logger.error("%s", problem)

    logger.error("%s", summary)
    raise SystemExit(EXIT_DATA_QUALITY)


def report_invalid_statements(problems: Sequence[str], logger: logging.Logger) -> None:
    """Abort if any input statement's account/period identity is missing or malformed.

    The partition (reporting.validation.partition_statements_by_metadata) owns the
    rationale and produces one "<path>: <reason>" string per file whose identity could
    not be established. The boundary lists every one, then a single
    SystemExit(EXIT_DATA_QUALITY) without writing a workbook. An empty sequence means
    every input's identity is sound and nothing is logged. Sequenced before the
    cross-file conflict check, which assumes a parseable identity on every input.
    """
    _list_then_abort(
        problems,
        f"{len(problems)} input statement(s) have a missing or malformed identity; no "
        "workbook written. A valid statement carries an Account number (Account "
        "Information) and a parseable reporting Period (Statement). Correct the "
        "file(s) above and rerun.",
        logger,
    )


def report_statement_input_conflicts(
    problems: Sequence[str], logger: logging.Logger
) -> None:
    """Abort unless the inputs are a single-account, non-overlapping set of statements.

    The detector (reporting.validation.detect_statement_input_conflicts) owns the
    rationale and produces one human-readable problem string per conflict. The
    boundary lists every one, then a single SystemExit(EXIT_DATA_QUALITY) -- no workbook
    written. An empty sequence means the input set is coherent and nothing is logged.
    Sequenced before merge_models so duplicate data never reaches FIFO and the merged
    diagnostics are not doubled.
    """
    _list_then_abort(
        problems,
        "Input statements do not form a single-account, non-overlapping set; no "
        "workbook written. Pass one account's statements, one period per year with no "
        "overlap to prevent double-counting trades.",
        logger,
    )


_RECONCILIATION_SAMPLE = 10


def format_reconciliation_sample(items: Sequence[SymbolReconciliation]) -> str:
    """Render a capped, count-honest sample of reconciliation entries for one log line.

    Shows at most _RECONCILIATION_SAMPLE entries; when more exist it prefixes "showing K
    of N", so a truncated list can never be read as the full set -- the signal the bare
    [:10] slice it replaces did not give. diff is included so a synthetic or value line
    keeps its magnitude visible.

    """
    head = "; ".join(
        f"{r.symbol} ({r.currency}) mine={r.computed} IBKR={r.ibkr} diff={r.diff}"
        for r in items[:_RECONCILIATION_SAMPLE]
    )
    if len(items) > _RECONCILIATION_SAMPLE:
        return f"showing {_RECONCILIATION_SAMPLE} of {len(items)}: {head}"
    return head


def report_reconciliation(report: ReconciliationReport, logger: logging.Logger) -> None:
    """Render the IBKR realized-P/L cross-check to the log, by divergence class.

    Every reconciled key is traced at DEBUG; the divergence classes are then surfaced on
    their own lines so distinct anomalies never share one. A gain/loss sign flip leads
    ahead of magnitude-only gaps, since it almost always marks a structural matching or
    basis bug rather than rounding. Synthesized-basis keys are reported apart at INFO:
    their agreement with IBKR is tautological, so a green reconciliation must not claim
    them, though a large diff on one still flags a real gap in its genuine portion. A
    non-Forex disposal with no IBKR Realized P/L is warned apart: IBKR elides Realized
    only for Forex, so a blank elsewhere is unexpected (corruption or an unmodeled
    format), distinct from the expected Forex skips that are not surfaced here.
    """
    for r in report.reconciled:
        logger.debug(
            "Reconciliation: %s (%s) - mine: %s, IBKR: %s, diff: %s (%s)",
            r.symbol,
            r.currency,
            r.computed,
            r.ibkr,
            r.diff,
            "OK" if r.is_match else "MISMATCH",
        )

    sign_flips = report.sign_flips
    if sign_flips:
        logger.warning(
            "Reconciliation: %d symbol(s) disagree with IBKR on gain/loss direction "
            "-- likely a matching or basis bug [%s]",
            len(sign_flips),
            format_reconciliation_sample(sign_flips),
        )

    value_diffs = report.value_diffs
    if value_diffs:
        logger.warning(
            "Reconciliation: %d symbol(s) disagree with IBKR realized P/L beyond "
            "rounding [%s]",
            len(value_diffs),
            format_reconciliation_sample(value_diffs),
        )

    if report.anomalous_elision:
        logger.warning(
            "Reconciliation: %d non-Forex disposal(s) carry no IBKR Realized P/L "
            "-- unexpected (IBKR elides it only for Forex); not cross-checked [%s]",
            len(report.anomalous_elision),
            ", ".join(f"{s} ({c})" for s, c in report.anomalous_elision),
        )

    if report.synthetic:
        logger.info(
            "Reconciliation: %d symbol(s) carry synthesized basis -- not independently "
            "confirmed [%s]",
            len(report.synthetic),
            format_reconciliation_sample(report.synthetic),
        )
