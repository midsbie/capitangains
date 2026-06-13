from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import TradeRow
from capitangains.reporting.fifo_domain import RealizedLine

logger = logging.getLogger(__name__)

# Our realized P/L is quantized to the cent once per sell (build_realized_line), while
# IBKR's per-trade column is unrounded. Summing N sells therefore accumulates at most
# half a cent of rounding each, so a true match must be tolerated to that bound rather
# than to a flat constant -- otherwise heavily-traded symbols raise false mismatches.
_HALF_CENT = Decimal("0.005")

# IBKR omits the per-trade Realized P/L only for Forex rows (an FX leg has no cost
# basis), so a blank there is expected and merely uncheckable. Every other asset class
# populates Realized on a closing sell, so a blank on a non-Forex disposal is anomalous
# (corruption or an unmodeled IBKR format) and is surfaced, even though it too cannot be
# cross-checked. Matched against TradeRow.asset_category verbatim, as IBKR spells it.
_ELISION_EXPECTED_CATEGORIES = frozenset({"Forex"})


@dataclass(frozen=True)
class SymbolReconciliation:
    """Realized-P/L cross-check for one (symbol, currency), in the trade currency.

    Both sides are expressed in the instrument's own currency, so no FX conversion
    stands between them: a difference beyond cent rounding is a genuine accounting gap,
    not a rate artifact. computed is our FIFO realized P/L; ibkr is the sum of IBKR's
    per-trade Realized P/L column. A None on either side means that side booked no
    realized P/L for the key -- itself a discrepancy worth surfacing.
    """

    symbol: str
    currency: Currency
    computed: Decimal | None
    ibkr: Decimal | None
    n_sells: int  # comparable (non-elided) sells behind ibkr; bounds rounding gap

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

        A wrong gain/loss direction is a stronger signal than a magnitude gap: it almost
        always means a structural matching or basis error, not rounding or a partial
        lot. Restricted to material, two-sided cases -- a None side is membership, not a
        sign flip, and a within-tolerance tie is not a divergence.
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

    reconciled holds the (symbol, currency) keys whose FIFO realized P/L was checked
    *independently* against IBKR's per-trade column -- is_match separates OK from
    MISMATCH. synthetic holds keys whose basis was synthesized from IBKR's own Basis
    (under --auto-fix-sell-gaps): the check is tautological (we back the cost out of the
    very figure IBKR used to compute its realized P/L), so they are surfaced as *not
    independently confirmed* rather than counted as a passing match.  incomplete holds
    keys whose closing sells were *all* elided by IBKR where that elision is *expected*
    (Forex carries no Realized P/L by design), leaving nothing to compare against; they
    are skipped quietly. A key with both an elided sell and a comparable one is NOT
    incomplete: it is reconciled on its comparable sells, since eliding one sell makes
    only that sell unverifiable, not its siblings. anomalous_elision is an orthogonal
    data-integrity flag, not a fourth trust partition: it holds every key carrying a
    *non-Forex* blank. IBKR populates Realized for every asset class but Forex, so a
    blank elsewhere is unexpected (corruption or an unmodeled format) and is warned. A
    key reconciled or synthetic on its comparable sells still appears here when one of
    its disposals is such a blank, since a corrupt cell warrants surfacing even where
    the rest ties out; the blank disposal itself stays uncross-checked regardless.
    """

    reconciled: list[SymbolReconciliation]
    synthetic: list[SymbolReconciliation]
    incomplete: list[tuple[str, Currency]]
    anomalous_elision: list[tuple[str, Currency]] = field(default_factory=list)

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
        booked no realized P/L (diff is None, so it is neither match nor flip).
        """
        return [r for r in self.reconciled if not r.is_match and not r.sign_diverged]


def reconcile_realized_against_ibkr(
    trades: Sequence[TradeRow],
    realized_lines: Sequence[RealizedLine],
    year: int,
) -> ReconciliationReport:
    """Cross-check our realized P/L against IBKR's, per symbol, in the trade currency.

    IBKR reports a per-trade Realized P/L in each instrument's own currency; summed per
    (symbol, currency) over the reporting year it reproduces IBKR's per-symbol
    subtotal. Comparing that against our FIFO total in the same currency needs no FX
    rate, so any difference beyond cent rounding is a real discrepancy (a missing buy
    lot, a gap-filled sell) rather than an artifact of the two sides converting to EUR
    by different methods. This is why the reconciliation reads the per-trade realized
    column and not the EUR Realized & Unrealized Performance Summary.

    Both inputs are filtered to year internally, so the contract is uniform: hand it the
    full trade stream and the full set of realized lines. The result is partitioned by
    how far each key can be trusted (see ReconciliationReport). A line whose basis was
    synthesized from IBKR's Basis is split out of the independent reconciled set: its
    agreement with IBKR is tautological, so a "green" reconciliation must not claim it.
    """
    # IBKR side: per (symbol, currency), sum the per-trade Realized P/L over closing
    # sells that carry a figure. A sell whose Realized IBKR elided contributes no
    # comparable value -- record only that the key HAS such a sell, so a symbol whose
    # sells are all elided can be reported as skipped, while one that also has a
    # comparable sell is still cross-checked on it. Eliding one sell makes only it
    # unverifiable, never its siblings (per-trade, not per-symbol).
    ibkr_realized: dict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
    n_sells: dict[tuple[str, Currency], int] = defaultdict(int)
    keys_with_elided_sell: set[tuple[str, Currency]] = set()
    anomalous_elision: set[tuple[str, Currency]] = set()
    for t in trades:
        if t.date.year != year:
            continue
        if t.quantity >= 0:
            continue  # an opening buy's realized P/L is definitionally zero

        key = (t.symbol, t.currency)
        if t.realized_pl_ccy is None:
            keys_with_elided_sell.add(key)
            if t.asset_category not in _ELISION_EXPECTED_CATEGORIES:
                anomalous_elision.add(key)
            continue

        n_sells[key] += 1
        ibkr_realized[key] += t.realized_pl_ccy

    # Our side: sum FIFO realized over the disposals that are independently comparable.
    # An IBKR-elided disposal is dropped here too (its ibkr_realized_elided line flag,
    # set at replay), so the same disposals are excluded from both sides and the
    # remaining sums compare like for like. A synthetic line still contributes to its
    # key's displayed total: it is surfaced as unconfirmed, not silently dropped.
    computed_realized: dict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
    synthetic_keys: set[tuple[str, Currency]] = set()
    for rl in realized_lines:
        if rl.sell_date.year != year:
            continue

        key = (rl.symbol, rl.currency)
        if rl.gap_fixed:
            synthetic_keys.add(key)
        # gap_fixed implies not elided. BasisSynthesisPolicy refuses to synthesize
        # without IBKR's Realized to vouch for the Basis, so this guard never drops a
        # synthetic line, keeping synthetic_keys a subset of `active` below.
        if not rl.ibkr_realized_elided:
            computed_realized[key] += rl.realized_pl_ccy

    def _entry(key: tuple[str, Currency]) -> SymbolReconciliation:
        sym, ccy = key
        return SymbolReconciliation(
            symbol=sym,
            currency=ccy,
            computed=computed_realized.get(key),
            ibkr=ibkr_realized.get(key),
            n_sells=n_sells.get(key, 0),
        )

    # Keys with comparable realized activity on either side. A pure open-buy position
    # has none and is not a discrepancy. Synthetic keys are surfaced as unconfirmed even
    # when they also carry an elided sell. The elided-only keys (no comparable or
    # synthetic sibling) are uncheckable, and incomplete skips the expected-Forex ones
    # quietly. anomalous_elision is orthogonal, already built above: it alarms every
    # non-Forex blank, including keys still reconciled on a comparable sell, since a
    # corrupt cell is worth surfacing even when the symbol otherwise ties out. Only
    # Forex legitimately omits Realized P/L, so only non-Forex blanks alarm.
    active = ibkr_realized.keys() | computed_realized.keys()
    reconciled = [_entry(k) for k in sorted(active - synthetic_keys)]
    synthetic = [_entry(k) for k in sorted(synthetic_keys)]
    uncheckable = keys_with_elided_sell - active - synthetic_keys
    incomplete = sorted(uncheckable - anomalous_elision)

    if incomplete:
        logger.info(
            "Reconciliation: %d symbol(s) skipped (IBKR realized P/L elided): %s",
            len(incomplete),
            ", ".join(f"{s} ({c})" for s, c in incomplete),
        )
    return ReconciliationReport(
        reconciled=reconciled,
        synthetic=synthetic,
        incomplete=incomplete,
        anomalous_elision=sorted(anomalous_elision),
    )
