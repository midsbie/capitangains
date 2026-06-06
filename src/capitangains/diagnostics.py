"""Boundary diagnostics for one report run.

These helpers translate the pipeline's computed results into user-facing log lines and,
where a precondition is violated, a process exit. They share one stance: accumulate
every problem of a kind, emit one ERROR per item plus a summary, then raise a single
SystemExit -- never fail-fast -- so the operator sees every defect in one pass.

The functions are pure with respect to the data they inspect (no file I/O, no argparse);
they take already-extracted domain objects and a logger. capitangains.pipeline sequences
them between the stages whose output they check.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Sequence

from capitangains.conv import parse_date
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel
from capitangains.reporting import (
    ExtractionDefect,
    ReconciliationReport,
    StatementPeriod,
    SymbolReconciliation,
    TradeRow,
    TransferRow,
    parse_statement_metadata,
)
from capitangains.reporting.fifo_domain import GapEvent, GapKey, GapResolution


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
    a summary ERROR, then a single SystemExit(2) -- no fail-fast, so the operator sees
    every problem in one pass. Only when the tie-out is clean does each SYNTHESIZED gap
    emit its per-lot audit WARNING (the synthetic cost basis is never silent).
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
        raise SystemExit(2)

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
    single SystemExit(2) -- no workbook written.
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
    raise SystemExit(2)


def report_missing_fx(
    missing: set[tuple[dt.date, str]], logger: logging.Logger
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
    raise SystemExit(2)


def report_transfer_ordering_collisions(
    trades: Sequence[TradeRow],
    transfers: Sequence[TransferRow],
    logger: logging.Logger,
) -> None:
    """Abort if a transfer shares a symbol's calendar day with other activity.

    Why this is fatal rather than resolved. FIFO lot creation and consumption are
    order-sensitive and keyed by (symbol, currency): the sequence in which buys, sells,
    transfer-ins (which seed a lot) and transfer-outs (which consume lots) are ingested
    decides which lots a disposal matches, and therefore its cost basis and realized
    P/L. IBKR's Trades section carries a full intraday timestamp (Date/Time), but its
    Transfers section carries only a Date -- no time, and the Code does not encode one
    -- so when a transfer lands on the same day as other order-sensitive activity in the
    same symbol, their true intraday order is simply not in the data. There is no honest
    way to deduce it.

    The alternative, fabricating a convention (transfer-in before all same-day trades,
    transfer-out after) and encoding it as priority constants in the sort key, would
    silently risk a wrong cost basis on exactly the figures this tool exists to get
    right, with no signal to the operator. Because a tax figure must be correct rather
    than plausibly guessed (the stance already taken for a missing FX rate or an
    unmatched sell) we refuse to assume an order IBKR did not provide, and halt so the
    operator can resolve the affected symbol against their own records.

    Why halting (and building no mitigation) is acceptable. The collision is rare to the
    point of being an edge case: a transfer is a position migration, whose normal
    lifecycle is migrate-then-hold (or migrate-then-trade-on-a-later-day), and
    receiving-broker settlement windows push a transfer and same-symbol trading apart.
    No convention or override mechanism is therefore built; this guard only states the
    cause when the rare case occurs, preserving today's findings for future readers.

    Scope. Only same (symbol, currency) matters, since consumption is keyed that way; a
    transfer sharing a day with unrelated symbols is independent and stays silent. A
    transfer colliding with another transfer of the same symbol on the same day is
    equally unorderable and is caught here too. Every collision is listed, then a single
    SystemExit(2), and a workbook is not written.
    """
    trades_by_key: dict[tuple[str, str, dt.date], int] = defaultdict(int)
    for t in trades:
        trades_by_key[(t.symbol, t.currency, t.date)] += 1

    transfers_by_key: dict[tuple[str, str, dt.date], int] = defaultdict(int)
    for tr in transfers:
        transfers_by_key[(tr.symbol, tr.currency, tr.date)] += 1

    # A transfer collides when its (symbol, currency, date) also holds a trade, or a
    # second transfer of the same symbol that day -- either way the intraday order is
    # undetermined.
    collisions = [
        (key, n_xfer, trades_by_key.get(key, 0))
        for key, n_xfer in transfers_by_key.items()
        if trades_by_key.get(key, 0) > 0 or n_xfer > 1
    ]
    if not collisions:
        return

    for (symbol, currency, date), n_xfer, n_trade in sorted(collisions):
        logger.error(
            "Unorderable same-day events for %s (%s) on %s: %d transfer(s) and %d "
            "trade(s) share the date, but IBKR gives transfers no intraday time, so "
            "their FIFO order cannot be determined.",
            symbol,
            currency,
            date,
            n_xfer,
            n_trade,
        )

    logger.error(
        "Transfer ordering is ambiguous for %d symbol-day(s); no workbook written. "
        "IBKR does not timestamp transfers, so a transfer landing on the same day as "
        "other activity in the same symbol cannot be ordered against it, and the cost "
        "basis would depend on an assumption the data does not support. Resolve the "
        "affected symbol(s) by hand, or adjust the inputs so the transfer and the "
        "same-day activity do not coincide.",
        len(collisions),
    )
    raise SystemExit(2)


def report_statement_input_conflicts(
    inputs: Sequence[str],
    models: Sequence[IbkrModel],
    logger: logging.Logger,
) -> None:
    """Abort unless the inputs are a single-account, non-overlapping set of statements.

    Multi-file mode exists to supply prior-year statements so FIFO has the buy lots for
    shares sold in the reporting year (see the module docstring). That is only coherent
    when the inputs are distinct, non-overlapping slices of one account's history:

    - Overlapping periods double-count. The same statement passed twice, or a combined
      multi-year export alongside a standalone year, feeds duplicate trades into FIFO --
      inflating proceeds and corrupting realized P/L on exactly the lots a filing
      depends on. There is no safe row-level de-duplication (IBKR can legitimately emit
      two distinct fills with identical symbol, time, quantity and price), so the only
      honest response is to reject the overlap rather than silently merge it.
    - Mixing accounts co-mingles unrelated positions into one report. The tool assumes a
      single account; statements from two accounts are out of scope, not a merge.
    - A missing or unparseable Account/Period makes disjointness unprovable. Consistent
      with the rest of the pipeline's fail-closed stance (a missing FX rate, an
      unmatched sell), an unverifiable precondition is a failure, not an assumption.

    Single-file runs have nothing to overlap and skip the check. Every conflict is
    listed, then a single SystemExit(2) -- no workbook written. Runs before merge_models
    so the duplicate data never reaches FIFO and the merged diagnostics are not doubled.
    """
    if len(inputs) <= 1:
        return

    problems: list[str] = []
    accounts: set[str] = set()
    periods: list[tuple[str, StatementPeriod]] = []  # only the cleanly-extracted ones

    for path, model in zip(inputs, models, strict=True):
        try:
            metadata = parse_statement_metadata(model)
        except DataQualityError as e:
            # Missing/malformed account or period: disjointness is unprovable for this
            # file. parse_statement_metadata names the first defect; the rest surface on
            # rerun once it is fixed.
            problems.append(f"{path}: {e}")
            continue

        accounts.add(metadata.account)
        periods.append((path, metadata.period))

    if len(accounts) > 1:
        problems.append(
            f"inputs span multiple accounts ({', '.join(sorted(accounts))}); this tool "
            "reports one account at a time."
        )

    # O(n^2) over the handful of statements a run ever takes; StatementPeriod owns the
    # closed-interval overlap test.
    for i, (p_path, p_period) in enumerate(periods):
        for q_path, q_period in periods[i + 1 :]:
            if p_period.overlaps(q_period):
                lo = max(p_period.start, q_period.start)
                hi = min(p_period.end, q_period.end)
                problems.append(
                    f"overlapping periods: {p_path} and {q_path} both cover "
                    f"{lo} to {hi}."
                )

    if not problems:
        return

    for problem in problems:
        logger.error("%s", problem)

    logger.error(
        "Input statements do not form a single-account, non-overlapping set; no "
        "workbook written. Pass one account's statements, one period per year with no "
        "overlap to prevent double-counting trades."
    )
    raise SystemExit(2)


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
    them, though a large diff on one still flags a real gap in its genuine portion.
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
    if report.synthetic:
        logger.info(
            "Reconciliation: %d symbol(s) carry synthesized basis -- not independently "
            "confirmed [%s]",
            len(report.synthetic),
            format_reconciliation_sample(report.synthetic),
        )
