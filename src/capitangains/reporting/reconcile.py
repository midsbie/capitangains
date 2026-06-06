from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from capitangains.reporting.extract import TradeRow
from capitangains.reporting.fifo_domain import RealizedLine

logger = logging.getLogger(__name__)

# Our realized P/L is quantized to the cent once per sell (build_realized_line), while
# IBKR's per-trade column is unrounded. Summing N sells therefore accumulates at most
# half a cent of rounding each, so a true match must be tolerated to that bound rather
# than to a flat constant -- otherwise heavily-traded symbols raise false mismatches.
_HALF_CENT = Decimal("0.005")


@dataclass(frozen=True)
class SymbolReconciliation:
    """Realized-P/L cross-check for one (symbol, currency), in the trade currency.

    Both sides are expressed in the instrument's own currency, so no FX conversion
    stands between them: a difference beyond cent rounding is a genuine accounting gap,
    not a rate artifact. ``computed`` is our FIFO realized P/L; ``ibkr`` is the sum of
    IBKR's per-trade ``Realized P/L`` column. A ``None`` on either side means that side
    booked no realized P/L for the key -- itself a discrepancy worth surfacing.
    """

    symbol: str
    currency: str
    computed: Decimal | None
    ibkr: Decimal | None
    n_sells: int  # closing trades behind ``ibkr``; bounds the accumulated rounding gap

    @property
    def diff(self) -> Decimal | None:
        if self.computed is None or self.ibkr is None:
            return None
        return (self.computed - self.ibkr).copy_abs()

    @property
    def tolerance(self) -> Decimal:
        return (self.n_sells + 1) * _HALF_CENT

    @property
    def is_match(self) -> bool:
        d = self.diff
        return d is not None and d <= self.tolerance

    @property
    def sign_diverged(self) -> bool:
        """True when both sides booked realized P/L but disagree on gain vs loss.

        A wrong gain/loss direction is a stronger signal than a magnitude gap: it
        almost always means a structural matching or basis error, not rounding or a
        partial lot. Restricted to material, two-sided cases -- a ``None`` side is
        membership, not a sign flip, and a within-tolerance tie is not a divergence.
        """
        if self.computed is None or self.ibkr is None or self.is_match:
            return False
        return (
            self.computed != 0
            and self.ibkr != 0
            and (self.computed > 0) != (self.ibkr > 0)
        )


@dataclass(frozen=True)
class ReconciliationReport:
    """Outcome of the IBKR realized-P/L cross-check, partitioned by trust level.

    ``reconciled`` holds the (symbol, currency) keys whose FIFO realized P/L was checked
    *independently* against IBKR's per-trade column -- ``is_match`` separates OK from
    MISMATCH. ``synthetic`` holds keys whose basis was synthesized from IBKR's own
    ``Basis`` (under ``--auto-fix-sell-gaps``): the check is tautological (we back the
    cost out of the very figure IBKR used to compute its realized P/L), so they are
    surfaced as *not independently confirmed* rather than counted as a passing match.
    ``incomplete`` holds keys IBKR elided realized P/L for on a closing trade, leaving
    nothing trustworthy to compare against; they are skipped.
    """

    reconciled: list[SymbolReconciliation]
    synthetic: list[SymbolReconciliation]
    incomplete: list[tuple[str, str]]

    @property
    def sign_flips(self) -> list[SymbolReconciliation]:
        """Independently-checked keys whose gain/loss direction disagrees with IBKR.

        The strongest mismatch class: a wrong sign almost always marks a structural
        matching or basis bug, so it is surfaced apart from mere magnitude gaps.
        """
        return [r for r in self.reconciled if r.sign_diverged]

    @property
    def value_diffs(self) -> list[SymbolReconciliation]:
        """Independently-checked keys that disagree, but only in magnitude.

        Every non-matching reconciled key that is not a sign flip -- a real gap (a
        missing lot, a basis proxy), or the one-sided membership case where a side
        booked no realized P/L (``diff`` is ``None``, so it is neither match nor flip).
        """
        return [r for r in self.reconciled if not r.is_match and not r.sign_diverged]


def reconcile_realized_against_ibkr(
    trades: Sequence[TradeRow],
    realized_lines: Sequence[RealizedLine],
    year: int,
) -> ReconciliationReport:
    """Cross-check our realized P/L against IBKR's, per symbol, in the trade currency.

    IBKR reports a per-trade ``Realized P/L`` in each instrument's own currency; summed
    per (symbol, currency) over the reporting year it reproduces IBKR's per-symbol
    subtotal. Comparing that against our FIFO total in the same currency needs no FX
    rate, so any difference beyond cent rounding is a real discrepancy (a missing buy
    lot, a gap-filled sell) rather than an artifact of the two sides converting to EUR
    by different methods. This is why the reconciliation reads the per-trade realized
    column and not the EUR ``Realized & Unrealized Performance Summary``.

    Both inputs are filtered to ``year`` internally, so the contract is uniform: hand it
    the full trade stream and the full set of realized lines. The result is partitioned
    by how far each key can be trusted (see ``ReconciliationReport``). A line whose
    basis was synthesized from IBKR's ``Basis`` is split out of the independent
    ``reconciled`` set: its agreement with IBKR is tautological, so a "green"
    reconciliation must not claim it.
    """
    ibkr_realized: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    n_sells: dict[tuple[str, str], int] = defaultdict(int)
    incomplete: set[tuple[str, str]] = set()
    for t in trades:
        if t.date.year != year:
            continue
        key = (t.symbol, t.currency)
        if t.quantity < 0:
            n_sells[key] += 1
        if t.realized_pl_ccy is None:
            # An opening trade's realized P/L is definitionally zero, so eliding it is
            # harmless; on a closing trade it makes the symbol's total unusable.
            if t.quantity < 0:
                incomplete.add(key)
            continue
        ibkr_realized[key] += t.realized_pl_ccy

    computed_realized: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    synthetic_keys: set[tuple[str, str]] = set()
    for rl in realized_lines:
        if rl.sell_date.year != year:
            continue
        key = (rl.symbol, rl.currency)
        computed_realized[key] += rl.realized_pl_ccy
        if rl.gap_fixed:
            synthetic_keys.add(key)

    # Reconcile only keys with realized activity: a pure open buy lot carries a zero
    # IBKR realized and no FIFO line, and is not a discrepancy. Keys IBKR elided on a
    # closing trade are dropped here and reported as ``incomplete`` instead.
    keys = {
        key
        for key in ibkr_realized.keys() | computed_realized.keys()
        if key not in incomplete
        and (n_sells.get(key, 0) > 0 or key in computed_realized)
    }

    def _entry(key: tuple[str, str]) -> SymbolReconciliation:
        sym, ccy = key
        return SymbolReconciliation(
            symbol=sym,
            currency=ccy,
            computed=computed_realized.get(key),
            ibkr=ibkr_realized.get(key),
            n_sells=n_sells.get(key, 0),
        )

    reconciled = [_entry(k) for k in sorted(keys - synthetic_keys)]
    synthetic = [_entry(k) for k in sorted(keys & synthetic_keys)]

    if incomplete:
        logger.info(
            "Reconciliation: %d symbol(s) skipped (IBKR realized P/L elided): %s",
            len(incomplete),
            ", ".join(sorted(f"{s} ({c})" for s, c in incomplete)),
        )
    return ReconciliationReport(
        reconciled=reconciled,
        synthetic=synthetic,
        incomplete=sorted(incomplete),
    )
