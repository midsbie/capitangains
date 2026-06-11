"""Application orchestration for the IBKR-to-Portugal capital-gains report.

run is the composition root: given a RunOptions it wires the statement parser, the
activity-statement source, the FIFO matcher (with its gap policy), FX conversion, the
soft IBKR reconciliation, and the Excel sink into one chronological pipeline and drives
them in order. It is framework-agnostic, as it depends on an explicit options value,
never on argparse, and the same pipeline can be exercised from a test or any other
front-end, not only the CLI.

Every stage that can reject the input is a fail-closed gate: the run aborts (exit 2, no
workbook) rather than emit a figure it cannot stand behind. The per-stage diagnostics
live in capitangains.diagnostics; this module only sequences them.

Delegation map:
- Parsing/model:    capitangains.model
- Data extraction:  capitangains.reporting.source / capitangains.reporting.extract
- Validation:       capitangains.reporting.validation
- FIFO matching:    capitangains.reporting.fifo
- FX conversion:    capitangains.reporting.fx
- Reconciliation:   capitangains.reporting.reconcile
- Output writing:   capitangains.reporting.report_sink
- Boundary reports: capitangains.diagnostics
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from capitangains.diagnostics import (
    parse_acknowledged_gaps,
    report_extraction_defects,
    report_gap_acknowledgments,
    report_invalid_statements,
    report_missing_fx,
    report_ordering_collisions,
    report_orphaned_foreign_tax,
    report_reconciliation,
    report_statement_input_conflicts,
    report_symbol_currency_violations,
    report_unattributed_income,
    report_unrecognized_sections,
)
from capitangains.errors import DataQualityError
from capitangains.model import IbkrStatementCsvParser, merge_models, merge_reports
from capitangains.reporting import (
    EventStream,
    FifoMatcher,
    FxTable,
    IbkrActivityStatementSource,
    ReportBuilder,
    detect_ordering_collisions,
    detect_orphaned_foreign_tax,
    detect_statement_input_conflicts,
    detect_symbol_currency_violations,
    detect_unattributed_income,
    detect_unrecognized_sections,
    partition_statements_by_metadata,
    reconcile_realized_against_ibkr,
)
from capitangains.reporting.gap_policy import build_gap_policy
from capitangains.reporting.report_sink import ExcelReportSink


@dataclass(frozen=True)
class RunOptions:
    """Explicit, framework-agnostic inputs for one report run.

    Mirrors the CLI surface but carries no argparse coupling: main translates the parsed
    arguments into this value and run consumes it. Verbosity is deliberately absent --
    logging is configured at the boundary before run is ever called.
    """

    inputs: Sequence[str]
    year: int
    fx_table: str | None
    locale: str
    output: str | None
    auto_fix_sell_gaps: str | None
    dry_run: bool
    broker_country: str = "IE"


def run(options: RunOptions) -> None:
    logger = logging.getLogger(__name__)

    # Parse the gap-acknowledgment spec before any file I/O so a malformed spec fails
    # fast (exit 2) without touching the statements.
    try:
        acknowledged = parse_acknowledged_gaps(options.auto_fix_sell_gaps)
    except DataQualityError as e:
        logger.error("%s", e)
        raise SystemExit(2) from e

    # Parse one or more CSVs
    inputs = options.inputs
    logger.info("Reading %d file(s): %s", len(inputs), ", ".join(inputs))

    parser = IbkrStatementCsvParser()
    models = []
    reports = []
    for p in inputs:
        m, rep = parser.parse_file(p)
        logger.debug(
            "Parsed %s: %d sections, %d subtables",
            p,
            len(m.sections),
            sum(len(subs) for subs in m.sections.values()),
        )
        models.append(m)
        reports.append(rep)

    # Parse errors are the most fundamental failure -- a file that did not parse has no
    # trustworthy identity or contents -- so halt on them before any further gate.
    parse_report = merge_reports(reports)
    parse_report.log_with(logger)
    if parse_report.has_errors:
        raise SystemExit(2)

    # Validate every input's statement identity (account + reporting period) as a
    # fail-closed precondition, regardless of file count, before trusting its contents.
    statements, invalid = partition_statements_by_metadata(inputs, models)
    report_invalid_statements(invalid, logger)

    # A multi-file run must further be a single-account, non-overlapping set of
    # statements; overlapping inputs would double-count trades into FIFO. The identity
    # gate above guarantees every period here is parseable. Detect before merging and
    # halt at the boundary if the inputs conflict.
    input_conflicts = detect_statement_input_conflicts(statements)
    report_statement_input_conflicts(input_conflicts, logger)

    model = merge_models(models)

    # Section-level coverage sweep: WARN on any merged section neither consumed by an
    # extractor nor allow-listed.
    report_unrecognized_sections(detect_unrecognized_sections(model), logger)

    # The source runs every section extractor, each of which accumulates its row-level
    # data-quality defects rather than failing on the first bad row, so a single run
    # surfaces every rejected row. The source unions those defects in extractor order;
    # halt once at the boundary (exit 2, no workbook) if any are present -- consistent
    # with the parse-error abort above.
    parsed = IbkrActivityStatementSource(asset_scope="stocks_etfs").read(model)
    report_extraction_defects(parsed.defects, logger)

    logger.info(
        "Extracted: %d trades, %d dividends, %d withholding, %d interest, %d transfers",
        len(parsed.trades),
        len(parsed.dividends),
        len(parsed.withholding),
        len(parsed.interest),
        len(parsed.transfers),
    )

    # Cross-row invariant (not per-row): one trade currency per symbol. The per-row
    # extraction defects above are already handled.
    violations = detect_symbol_currency_violations(parsed.trades, parsed.transfers)
    report_symbol_currency_violations(violations, logger)

    # An untimed event, such as a transfer (IBKR never timestamps transfers) or a trade
    # row with a date-only Date/Time, sharing a symbol's day with other order-sensitive
    # activity cannot be ordered for FIFO. Detect and halt rather than guess -- refer to
    # the detector's rationale.
    collisions = detect_ordering_collisions(parsed.trades, parsed.transfers)
    report_ordering_collisions(collisions, logger)

    # Build FIFO realized. The composition root owns gap-policy assembly: the matcher
    # itself stays agnostic of how gaps are resolved.
    matcher = FifoMatcher(gap_policy=build_gap_policy(acknowledged))

    # Replay trades and transfers through the matcher as one chronological stream so
    # FIFO lot creation/consumption respects actual event ordering. EventStream owns
    # that ordering (it sorts at construction), so the matcher's ingestion precondition
    # is an invariant of the type rather than a contract this call site must honor.
    # Same-day same-symbol ordering collisions were already rejected above.
    realized = EventStream(parsed.trades, parsed.transfers).replay(matcher)

    logger.info(
        "FIFO matching: %d trades processed, %d realized lines generated",
        len(parsed.trades),
        len(realized),
    )

    report_gap_acknowledgments(matcher.gap_events, acknowledged, logger)

    # Build the year-scoped report. ReportBuilder owns its year invariant: each ingest
    # method retains only rows dated in options.year, so the full multi-file parse
    # (needed upstream to seed FIFO) is handed over whole and the builder does the
    # scoping. See ReportBuilder.set_transfers for why scoping transfers is display-only
    # and cannot move a tax figure.
    rb = ReportBuilder(year=options.year, broker_country=options.broker_country)
    rb.add_realized_lines(realized)
    rb.set_dividends(parsed.dividends)
    rb.set_withholding(parsed.withholding)
    rb.set_syep_interest(parsed.syep_interest)
    rb.set_interest(parsed.interest)
    rb.set_transfers(parsed.transfers)

    logger.info(
        "Report built: %d realized lines, %d dividend lines, %d withholding lines",
        len(rb.realized_lines),
        len(rb.dividends),
        len(rb.withholding),
    )

    fx: FxTable | None = None
    if options.fx_table:
        try:
            fx = FxTable.from_csv(options.fx_table)
        except Exception as e:
            # A missing or unparseable FX table is a setup failure, not a defect in the
            # statement data. It exits 1, a class apart from the curated gates' exit 2,
            # and is surfaced as one clean ERROR rather than a raw crash.
            logger.error(
                "Failed to prepare FX conversion from %s: %s", options.fx_table, e
            )
            raise SystemExit(1) from e

    rb.convert_eur(fx)
    report_missing_fx(rb.fx_missing, logger)

    # Soft coverage checks on the Quadro 8A income lines (warn, do not abort): income
    # whose source country could not be identified, and foreign tax with no matching
    # gross income. The figures are correct; only the attribution is incomplete, so the
    # operator fills the gaps by hand rather than losing the whole report. Bound once so
    # both detectors fold the rows a single time; the sink later recomputes the same
    # deterministic fold when it writes the sheet.
    quadro_8a = rb.quadro_8a
    report_unattributed_income(detect_unattributed_income(quadro_8a), logger)
    report_orphaned_foreign_tax(detect_orphaned_foreign_tax(quadro_8a), logger)

    # Soft reconciliation: cross-check our realized P/L against IBKR's own per-trade
    # `Realized P/L`, per symbol, in each instrument's trade currency. No FX stands
    # between the two sides, so a disagreement beyond cent rounding is a real accounting
    # gap rather than a rate artifact. reconcile_realized_against_ibkr filters both
    # sides to the reporting year, and report_statement_input_conflicts (above) has
    # already proven any multi-file set single-account and non-overlapping, so each
    # year's trades are counted once whatever the file count. Multi-file is in fact the
    # stronger check: prior-year files seed FIFO with the real opening lots a cross-year
    # sale's basis depends on, where single-file would have only a synthesized
    # (tautological) or absent basis.
    try:
        report = reconcile_realized_against_ibkr(
            parsed.trades, rb.realized_lines, options.year
        )
        report_reconciliation(report, logger)
    except Exception:
        logger.exception("Reconciliation failed; continuing without it.")

    out_path = (
        Path(options.output) if options.output else Path(f"report_{options.year}.xlsx")
    )

    if options.dry_run:
        logger.info(
            "Dry run: all checks passed; no workbook written (would write to %s).",
            out_path,
        )
        return

    sink = ExcelReportSink(out_path=out_path, locale=options.locale)
    out_path = sink.write(rb)
    logger.info("Wrote workbook to %s", out_path)
