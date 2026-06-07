"""Pure data-quality detectors over a statement set and its extracted events.

Each function inspects already-parsed inputs and RETURNS its findings with no logging or
process-exit side effects; an empty result means clean. Translating a non-empty result
into user-facing ERROR lines and an exit code is the boundary's job (see
capitangains.diagnostics); keeping detection pure makes every invariant unit-testable
without capturing logs or trapping SystemExit.

Two scopes live here, both pure detection: invariants over extracted trade/transfer rows
(symbol-currency uniqueness, transfer ordering) and the identity and coherence of a
statement input set. Each file's account/period identity is validated unconditionally as
a fail-closed precondition before its contents are trusted, then the cross-file
coherence of the set (single account, non-overlapping periods) is enforced.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from .extract import StatementMetadata, TradeRow, TransferRow, parse_statement_metadata


def detect_symbol_currency_violations(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> dict[str, frozenset[str]]:
    """Find symbols that appear under more than one trade currency.

    Design choice: IBKR symbols are treated as exchange-specific identifiers, each
    denominated in a single currency.  If the same ticker appears on exchanges with
    different currencies (e.g. "RY" on NYSE/USD and TSX/CAD), the CSV data must
    disambiguate them with distinct symbols.  Allowing multiple currencies per symbol
    would make the per-symbol summary incoherent -- trade-currency columns can only
    represent one denomination, while EUR columns aggregate across all, producing
    rows that cannot be reconciled.

    Returns each offending symbol mapped to the (>1) currencies seen for it; an empty
    mapping means the invariant holds.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    events: Sequence[TradeRow | TransferRow] = [*trades, *transfers]
    for event in events:
        seen[event.symbol].add(event.currency)
    return {sym: frozenset(ccys) for sym, ccys in seen.items() if len(ccys) > 1}


@dataclass(frozen=True, order=True)
class TransferOrderingCollision:
    """One (symbol, currency, day) whose intraday FIFO order is undetermined.

    n_transfers transfers and n_trades trades of the same symbol and currency fall on
    date. Because IBKR gives transfers no intraday time, their order against same-day
    same-symbol activity (or against a second same-day transfer) cannot be deduced from
    the data. order=True compares fields in definition order, and (symbol, currency,
    date) is unique per collision, so that triple alone determines the sort. A report
    over a set of collisions is therefore deterministic.
    """

    symbol: str
    currency: str
    date: dt.date
    n_transfers: int
    n_trades: int


def detect_transfer_ordering_collisions(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> list[TransferOrderingCollision]:
    """Find transfers whose FIFO order against same-day activity is undetermined.

    FIFO lot creation and consumption are order-sensitive and keyed by (symbol,
    currency): the sequence in which buys, sells, transfer-ins (which seed a lot) and
    transfer-outs (which consume lots) are ingested decides which lots a disposal
    matches, and thus its cost basis and realized P/L. IBKR's Trades section carries a
    full intraday timestamp (Date/Time), but its Transfers section carries only a Date
    lacking a time component (and the Code does not encode one) so when a transfer lands
    on the same day as other order-sensitive activity in the same symbol, their true
    intraday order is simply not in the data. Consequently, there is no honest way to
    deduce it.

    Scope. Only same (symbol, currency) matters, since consumption is keyed that way; a
    transfer sharing a day with unrelated symbols is independent and is not reported. A
    transfer colliding with another transfer of the same symbol on the same day is
    equally unorderable and is reported too.

    Returns every collision (sorted); an empty list means every transfer is orderable.
    The decision to halt rather than fabricate an order is the boundary's (see
    diagnostics.report_transfer_ordering_collisions).

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
        TransferOrderingCollision(
            symbol=symbol,
            currency=currency,
            date=date,
            n_transfers=n_xfer,
            n_trades=trades_by_key.get((symbol, currency, date), 0),
        )
        for (symbol, currency, date), n_xfer in transfers_by_key.items()
        if trades_by_key.get((symbol, currency, date), 0) > 0 or n_xfer > 1
    ]
    return sorted(collisions)


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
