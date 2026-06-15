"""Pure data-quality detectors over a statement set and its extracted events.

Each function inspects already-parsed inputs and RETURNS its findings with no logging or
process-exit side effects; an empty result means clean. Translating a non-empty result
into user-facing ERROR lines and an exit code is the boundary's job (see
capitangains.diagnostics); keeping detection pure makes every invariant unit-testable
without capturing logs or trapping SystemExit.

Several scopes live here, all pure detection: invariants over extracted trade/transfer
rows (symbol-currency uniqueness, same-day event ordering), the identity and coherence
of a statement input set, and the built Anexo J Quadro 8A income lines (source-country
attribution). Each file's account/period identity is validated unconditionally as a
fail-closed precondition before its contents are trusted, then the cross-file coherence
of the set (single account, non-overlapping periods) is enforced.

parse_acknowledged_gaps also lives here. It parses the operator's raw
--auto-fix-sell-gaps spec into a set of keys, raising DataQualityError on a malformed
token. Its subject is a CLI string rather than parsed statement data, but it keeps the
detectors' pure, no-logging, no-exit discipline, so the boundary maps its raise to an
exit exactly as it does a detector's findings.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from capitangains.conv import Currency
from capitangains.conv.ibkr import has_intraday_time, parse_date
from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from .extract import StatementMetadata, TradeRow, TransferRow, parse_statement_metadata
from .extract.sections import CONSUMED_SECTIONS, IGNORED_SECTIONS
from .fifo_domain import GapKey
from .quadro_8a import Quadro8ALine


def detect_symbol_currency_violations(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> dict[str, frozenset[Currency]]:
    """Find symbols that appear under more than one trade currency.

    Design choice: IBKR symbols are treated as exchange-specific identifiers, each
    denominated in a single currency.  If the same ticker appears on exchanges with
    different currencies (e.g. "RY" on NYSE/USD and TSX/CAD), the CSV data must
    disambiguate them with distinct symbols.  Allowing multiple currencies per symbol
    would make the per-symbol summary incoherent -- trade-currency columns can only
    represent one denomination, while EUR columns aggregate across all, producing rows
    that cannot be reconciled.

    Returns each offending symbol mapped to the (>1) currencies seen for it; an empty
    mapping means the invariant holds.
    """
    seen: dict[str, set[Currency]] = defaultdict(set)
    events: Sequence[TradeRow | TransferRow] = [*trades, *transfers]
    for event in events:
        seen[event.symbol].add(event.currency)
    return {sym: frozenset(ccys) for sym, ccys in seen.items() if len(ccys) > 1}


@dataclass(frozen=True, order=True)
class OrderingCollision:
    """One (symbol, currency, day) whose intraday FIFO order is undetermined.

    A group of order-sensitive events of the same symbol and currency falls on date, and
    at least one of them carries no intraday time: IBKR gives transfers only a date, and
    some trade rows carry a date-only Date/Time. An event with no time cannot be placed
    relative to the others, so the group's FIFO sequence, and therefore the cost basis
    it produces, is not in the data. n_trades counts all same-day trades,
    n_untimed_trades the date-only subset, and n_transfers the transfers (each itself
    untimed). order=True compares fields in definition order, and (symbol, currency,
    date) is unique per collision, so that triple alone determines the sort; a report
    over a set of collisions is therefore deterministic.
    """

    symbol: str
    currency: Currency
    date: dt.date
    n_trades: int
    n_untimed_trades: int
    n_transfers: int


def detect_ordering_collisions(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> list[OrderingCollision]:
    """Find (symbol, currency, day) groups whose intraday FIFO order is undetermined.

    FIFO lot creation and consumption are order-sensitive and keyed by (symbol,
    currency): the sequence in which buys, sells, transfer-ins (which seed a lot) and
    transfer-outs (which consume lots) are ingested decides which lots a disposal
    matches, and thus its cost basis and realized P/L. That sequence is recoverable only
    from intraday timestamps. IBKR's Transfers section carries only a Date (no time, and
    the Code encodes none), and some Trades rows carry a date-only Date/Time, so an
    untimed event sharing a day with other same-symbol activity has no order in the
    data.

    Scope. Only same (symbol, currency, date) matters, since consumption is keyed that
    way; an untimed event sharing a day with unrelated symbols is independent and is not
    reported. A group collides when it holds at least two order-sensitive events and at
    least one is untimed (a transfer or a date-only trade): two fully timestamped trades
    remain orderable by their times and are not a collision.

    Returns every collision (sorted); an empty list means every event is orderable. The
    decision to halt rather than fabricate an order is the boundary's (see
    diagnostics.report_ordering_collisions).
    """
    trades_by_key: dict[tuple[str, Currency, dt.date], int] = defaultdict(int)
    untimed_trades_by_key: dict[tuple[str, Currency, dt.date], int] = defaultdict(int)
    for t in trades:
        key = (t.symbol, t.currency, t.date)
        trades_by_key[key] += 1
        if not has_intraday_time(t.datetime_str):
            untimed_trades_by_key[key] += 1

    transfers_by_key: dict[tuple[str, Currency, dt.date], int] = defaultdict(int)
    for tr in transfers:
        transfers_by_key[(tr.symbol, tr.currency, tr.date)] += 1

    collisions: list[OrderingCollision] = []
    for key in set(trades_by_key) | set(transfers_by_key):
        symbol, currency, date = key
        n_trades = trades_by_key.get(key, 0)
        n_untimed = untimed_trades_by_key.get(key, 0)
        n_transfers = transfers_by_key.get(key, 0)
        # Undetermined only when more than one event shares the day AND at least one
        # carries no time (every transfer, plus any date-only trade). Two timestamped
        # trades are orderable, so a trades-only timed day is not a collision.
        if n_trades + n_transfers >= 2 and n_untimed + n_transfers >= 1:
            collisions.append(
                OrderingCollision(
                    symbol=symbol,
                    currency=currency,
                    date=date,
                    n_trades=n_trades,
                    n_untimed_trades=n_untimed,
                    n_transfers=n_transfers,
                )
            )

    return sorted(collisions)


@dataclass(frozen=True, order=True)
class UnrecognizedSection:
    """One section present in the merged statement that no extractor consumes.

    Neither consumed (CONSUMED_SECTIONS) nor allow-listed (IGNORED_SECTIONS). A soft
    signal, not a defect: IBKR adds and renames sections over time and we cannot tell a
    renamed data-bearing section (silent data loss) from a benign new one. name leads
    the fields so order=True sorts deterministically; the counts quantify what is going
    unconsumed so a maintainer can judge whether real data is being dropped.
    """

    name: str
    subtable_count: int
    row_count: int


def detect_unrecognized_sections(model: IbkrModel) -> list[UnrecognizedSection]:
    """Sections present in the model that no extractor consumes and aren't ignored.

    Present keys minus CONSUMED_SECTIONS minus IGNORED_SECTIONS, with subtable and row
    counts, sorted by name. Pure detection (empty list == full coverage); the boundary
    only WARNs, see diagnostics.report_unrecognized_sections.
    """
    unknown = set(model.sections) - CONSUMED_SECTIONS - IGNORED_SECTIONS
    return sorted(
        UnrecognizedSection(
            name=name,
            subtable_count=len(model.sections[name]),
            row_count=sum(len(sub.rows) for sub in model.sections[name]),
        )
        for name in unknown
    )


def detect_unattributed_income(lines: Sequence[Quadro8ALine]) -> list[Quadro8ALine]:
    """Quadro 8A income lines whose source country could not be identified.

    A dividend or payment-in-lieu whose description has no parseable ISIN, or whose
    withholding carries no " - XX Tax" suffix, folds into an empty-country group: the
    EUR gross and foreign tax are correct, but the line cannot name the source country
    the Anexo J form requires. Interest is always attributed to the injected broker
    country, so only dividend-side lines can surface here. Returns the offending lines
    in the builder's order; empty means every line names a source country. The boundary
    only WARNs (the figures stand, only the label is missing), see
    diagnostics.report_unattributed_income.
    """
    return [line for line in lines if not line.country]


def detect_orphaned_foreign_tax(lines: Sequence[Quadro8ALine]) -> list[Quadro8ALine]:
    """Quadro 8A lines carrying foreign tax with no matching gross income.

    The gross and tax sides are grouped independently and merged by (kind, country), so
    when a dividend's description has no parseable ISIN while its withholding carries a
    " - XX Tax" suffix (or the reverse), the two split into a gross-only line under the
    empty country and a tax-only line under "XX". detect_unattributed_income surfaces
    the former; this surfaces the latter, so neither half of a split passes silently. A
    tax-only line also legitimately arises from a cross-year timing mismatch (tax
    withheld in a year whose income is reported elsewhere); either way the figure is
    correct but the operator must reconcile it against its income by hand. Returns the
    offending lines in the builder's order; empty means every taxed line has matching
    gross. The boundary only WARNs (see diagnostics.report_orphaned_foreign_tax).
    """
    return [line for line in lines if line.gross_eur == 0 and line.tax_eur != 0]


@dataclass(frozen=True)
class StatementInput:
    """One input file paired with its parsed statement identity.

    The product of validating a file's identity (see
    partition_statements_by_metadata): a path whose account and reporting period are
    known-good. The cross-file coherence checks consume these records, so by the time
    they run every period is parseable and disjointness is provable.
    """

    path: str
    metadata: StatementMetadata


def partition_statements_by_metadata(
    inputs: Sequence[str], models: Sequence[IbkrModel]
) -> tuple[list[StatementInput], list[str]]:
    """Validate each file's statement identity, partitioning valid from malformed.

    A statement's identity is its account and reporting period. Validating it is a
    fail-closed precondition applied to every input regardless of file count: trust a
    document's contents only once its identity is established. (Single-file runs do not
    use the period for selection today, but a missing or malformed identity still marks
    an input the tool cannot vouch for. This is consistent with the pipeline's stance
    that an unverifiable precondition is a failure, not an assumption.)

    Owns the sole per-file parse_statement_metadata call, which names the first defect
    of a malformed file, with the rest surfacing on rerun (once fixed.) Returns the
    cleanly-identified inputs as StatementInput records, and one "<path>: <reason>"
    string per file with a missing or malformed identity. This list IS the
    invalid-statements finding the boundary reports (for further information, refer to
    diagnostics.report_invalid_statements), and an empty list means every input's
    identity is sound.
    """
    statements: list[StatementInput] = []
    problems: list[str] = []

    for path, model in zip(inputs, models, strict=True):
        try:
            metadata = parse_statement_metadata(model)
        except DataQualityError as e:
            problems.append(f"{path}: {e}")
            continue
        statements.append(StatementInput(path=path, metadata=metadata))

    return statements, problems


def detect_statement_input_conflicts(
    statements: Sequence[StatementInput],
) -> list[str]:
    """Find reasons a multi-file input set is not one account's disjoint statements.

    Multi-file mode exists to supply prior-year statements so FIFO has the buy lots for
    shares sold in the reporting year. That is only coherent when the inputs are
    distinct, non-overlapping slices of one account's history:

    - Overlapping periods double-count. The same statement passed twice, or a combined
      multi-year export alongside a standalone year, feeds duplicate trades into FIFO --
      inflating proceeds and corrupting realized P/L on exactly the lots a filing
      depends on. There is no safe row-level de-duplication (IBKR can legitimately emit
      two distinct fills with identical symbol, time, quantity and price), so an overlap
      is reported rather than silently merged.
    - Mixing accounts co-mingles unrelated positions into one report. The tool assumes a
      single account; statements from two accounts are out of scope, not a merge.

    Consumes the StatementInput records partition_statements_by_metadata has already
    validated, so every period here is parseable; missing or malformed identity is that
    function's concern, not this one's. Single-file runs have nothing to overlap and
    return no conflicts. Returns one human-readable problem string per conflict (empty
    means the set is coherent); the boundary lists them and halts (see
    diagnostics.report_statement_input_conflicts).
    """
    if len(statements) <= 1:
        return []

    problems: list[str] = []

    accounts = {s.metadata.account for s in statements}
    if len(accounts) > 1:
        problems.append(
            f"inputs span multiple accounts ({', '.join(sorted(accounts))}); this tool "
            "reports one account at a time."
        )

    # O(n^2) over the handful of statements a run ever takes; StatementPeriod owns the
    # closed-interval overlap test.
    for i, p in enumerate(statements):
        for q in statements[i + 1 :]:
            if p.metadata.period.overlaps(q.metadata.period):
                lo = max(p.metadata.period.start, q.metadata.period.start)
                hi = min(p.metadata.period.end, q.metadata.period.end)
                problems.append(
                    f"overlapping periods: {p.path} and {q.path} both cover "
                    f"{lo} to {hi}."
                )

    return problems


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
