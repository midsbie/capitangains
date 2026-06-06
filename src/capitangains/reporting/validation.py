"""Pure data-quality detectors over a statement set and its extracted events.

Each function inspects already-parsed inputs and RETURNS its findings with no logging or
process-exit side effects; an empty result means clean. Translating a non-empty result
into user-facing ERROR lines and an exit code is the boundary's job (see
capitangains.diagnostics); keeping detection pure makes every invariant unit-testable
without capturing logs or trapping SystemExit.

Two scopes live here, both pure detection: invariants over extracted trade/transfer rows
(symbol-currency uniqueness, transfer ordering) and the coherence of a multi-file input
set (single account, non-overlapping periods).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from capitangains.errors import DataQualityError
from capitangains.model import IbkrModel

from .extract import StatementPeriod, TradeRow, TransferRow, parse_statement_metadata


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

    ``n_transfers`` transfers and ``n_trades`` trades of the same symbol and currency
    fall on ``date``. Because IBKR gives transfers no intraday time, their order against
    same-day same-symbol activity (or against a second same-day transfer) cannot be
    deduced from the data. ``order=True`` compares fields in definition order, and
    (symbol, currency, date) is unique per collision, so that triple alone determines
    the sort -- a report over a set of collisions is deterministic.
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
    matches, and thus its cost basis and realized P/L. IBKR's Trades section carries
    a full intraday timestamp (Date/Time), but its Transfers section carries only a Date
    -- no time, and the Code does not encode one -- so when a transfer lands on the same
    day as other order-sensitive activity in the same symbol, their true intraday order
    is simply not in the data. There is no honest way to deduce it.

    Scope. Only same (symbol, currency) matters, since consumption is keyed that way; a
    transfer sharing a day with unrelated symbols is independent and is not reported. A
    transfer colliding with another transfer of the same symbol on the same day is
    equally unorderable and is reported too.

    Returns every collision (sorted); an empty list means every transfer is orderable.
    The decision to halt rather than fabricate an order is the boundary's (see
    ``diagnostics.report_transfer_ordering_collisions``).
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


def detect_statement_input_conflicts(
    inputs: Sequence[str], models: Sequence[IbkrModel]
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
    - A missing or unparseable Account/Period makes disjointness unprovable. Consistent
      with the rest of the pipeline's fail-closed stance (a missing FX rate, an
      unmatched sell), an unverifiable precondition is a failure, not an assumption.

    Single-file runs have nothing to overlap and return no conflicts. Returns one
    human-readable problem string per conflict (empty means the set is coherent); the
    boundary lists them and halts (see
    ``diagnostics.report_statement_input_conflicts``).
    """
    if len(inputs) <= 1:
        return []

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

    return problems
