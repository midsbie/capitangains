"""Resolve each transferred-in lot's cost basis from the Open Positions snapshots.

A Transfers row carries only the transfer-day Market Value, not what was paid; the true
cost basis lives in the Open Positions 'Cost Basis' column of the statement covering the
transfer. This builds a per-statement index of those snapshots (before merge_models
drops the per-file provenance each snapshot's period depends on), resolves each transfer
against it by period, quantity, and same-period trading activity, then writes the
resolved basis onto the TransferRow in place for FIFO seeding.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.model import IbkrModel

from .extract import (
    ExtractionDefect,
    OpenPositionRow,
    StatementPeriod,
    TradeRow,
    TransferRow,
    parse_open_positions,
)
from .validation import StatementInput


class TransferBasisIndex:
    """Open Positions snapshots per (symbol, currency), queryable by a transfer.

    Each entry pairs a statement's reporting period with one Open Positions snapshot of
    a symbol in that period. resolve returns the cost basis of the snapshot whose period
    contains the transfer date, whose quantity equals the transfer's, AND whose period
    saw no same-symbol trading; the three guards together pin a transferred lot to its
    own basis:

    - Period-containment anchors to the transfer's own year, so a symbol transferred one
      year and added to in a later one is read from the transfer-year snapshot (the
      quantity held just after the transfer), never a later, blended one.
    - The quantity guard rejects a snapshot blended within the same year by a separate
      purchase or partial sale, where the snapshot quantity no longer equals the
      transferred quantity and so its basis is not this lot's alone.
    - The no-trading guard (see mark_trade_activity) closes the case the quantity guard
      alone cannot: a purchase and an offsetting sale that net the period-end quantity
      back to the transferred amount leave the snapshot at the matching quantity but
      with a blended basis. A period-end snapshot, Summary or per-lot, cannot recover
      the original basis once trading has consumed or added to the lot, so any
      same-symbol trade in the period disqualifies its snapshot rather than trusting a
      possibly blended basis.

    A miss on any guard returns None, which the caller treats as unresolved (FIFO falls
    back to Market Value and flags the lot).
    """

    def __init__(self) -> None:
        self._snapshots: dict[
            tuple[str, Currency], list[tuple[StatementPeriod, OpenPositionRow]]
        ] = defaultdict(list)
        self._periods: set[StatementPeriod] = set()
        self._traded: set[tuple[str, Currency, StatementPeriod]] = set()

    def add(self, period: StatementPeriod, row: OpenPositionRow) -> None:
        self._snapshots[(row.symbol, row.currency)].append((period, row))
        self._periods.add(period)

    def mark_trade_activity(self, trades: Iterable[TradeRow]) -> None:
        """Record every (symbol, currency, period) a trade falls in, so resolve can
        refuse a snapshot whose period saw same-symbol trading (the no-trading guard in
        the class docstring). Periods are disjoint, so a date lands in at most one.
        """
        for t in trades:
            for period in self._periods:
                if period.contains(t.date):
                    self._traded.add((t.symbol, t.currency, period))

    def resolve(self, transfer: TransferRow) -> Decimal | None:
        """The transfer's true cost basis, or None when no snapshot resolves.

        None is the signal that no true basis exists, not the Market Value fallback:
        FIFO needs it to flag the lot basis_unresolved and fail closed on a reported
        disposal. Returning market_value here would erase that distinction and seed a
        wrong basis silently.
        """
        for period, row in self._snapshots.get(
            (transfer.symbol, transfer.currency), ()
        ):
            if (
                period.contains(transfer.date)
                and row.quantity == transfer.quantity
                and (transfer.symbol, transfer.currency, period) not in self._traded
            ):
                return row.cost_basis
        return None


def build_transfer_basis_index(
    statements: Sequence[StatementInput],
    models_by_path: dict[str, IbkrModel],
) -> tuple[TransferBasisIndex, list[ExtractionDefect]]:
    """Index every statement's Open Positions snapshots by (symbol, currency).

    Built from the per-file models BEFORE merge_models, which concatenates subtables and
    so erases the per-statement period each snapshot must be tagged with. The
    input-conflict gate has already proven every StatementInput.path unique, so it is a
    sound key into models_by_path. Accumulates the extractor's per-row defects for the
    boundary (WARN-only) rather than raising.
    """
    index = TransferBasisIndex()
    defects: list[ExtractionDefect] = []
    for s in statements:
        rows, row_defects = parse_open_positions(models_by_path[s.path])
        defects.extend(row_defects)
        for row in rows:
            index.add(s.metadata.period, row)
    return index, defects


def resolve_transfer_basis(
    transfers: Iterable[TransferRow], index: TransferBasisIndex
) -> None:
    """Write each transfer's resolved Open Positions cost basis onto it in place.

    Mutates TransferRow.cost_basis (None when unresolved), mirroring the FX pass that
    fills DividendRow.amount_eur in place rather than rebuilding the rows.
    """
    for t in transfers:
        t.cost_basis = index.resolve(t)
