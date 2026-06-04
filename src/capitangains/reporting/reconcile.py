from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from capitangains.reporting.extract import TradeRow
from capitangains.reporting.report_builder import SymbolTotals

logger = logging.getLogger(__name__)

# Our realized P/L is quantized to the cent once per sell (build_realized_line), while
# IBKR's per-trade column is unrounded. Summing N sells therefore accumulates at most
# half a cent of rounding each, so a true match must be tolerated to that bound rather
# than to a flat constant — otherwise heavily-traded symbols raise false mismatches.
_HALF_CENT = Decimal("0.005")


@dataclass(frozen=True)
class SymbolReconciliation:
    """Realized-P/L cross-check for one (symbol, currency), in the trade currency.

    Both sides are expressed in the instrument's own currency, so no FX conversion
    stands between them: a difference beyond cent rounding is a genuine accounting gap,
    not a rate artifact. ``computed`` is our FIFO realized P/L; ``ibkr`` is the sum of
    IBKR's per-trade ``Realized P/L`` column. A ``None`` on either side means that side
    booked no realized P/L for the key — itself a discrepancy worth surfacing.
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


def reconcile_realized_against_ibkr(
    trades: Sequence[TradeRow],
    symbol_totals: Mapping[str, SymbolTotals],
    year: int,
) -> list[SymbolReconciliation]:
    """Cross-check our realized P/L against IBKR's, per symbol, in the trade currency.

    IBKR reports a per-trade ``Realized P/L`` in each instrument's own currency; summed
    per (symbol, currency) over the reporting year it reproduces IBKR's per-symbol
    subtotal. Comparing that against our FIFO total in the same currency needs no FX
    rate, so any difference beyond cent rounding is a real discrepancy (a missing buy
    lot, a gap-filled sell) rather than an artifact of the two sides converting to EUR
    by different methods. This is why the reconciliation reads the per-trade realized
    column and not the EUR ``Realized & Unrealized Performance Summary``.

    A symbol whose IBKR realized P/L is elided on any closing trade cannot be trusted
    and is omitted from the result (logged once as INFO).
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

    computed_realized: dict[tuple[str, str], Decimal] = {
        (sym, ccy): tot.realized
        for sym, st in symbol_totals.items()
        for ccy, tot in st.by_currency.items()
    }

    # Reconcile only keys with realized activity: a pure open buy lot carries a zero
    # IBKR realized and no FIFO line, and is not a discrepancy.
    keys = {
        key
        for key in ibkr_realized.keys() | computed_realized.keys()
        if key not in incomplete
        and (n_sells.get(key, 0) > 0 or key in computed_realized)
    }
    results = [
        SymbolReconciliation(
            symbol=sym,
            currency=ccy,
            computed=computed_realized.get((sym, ccy)),
            ibkr=ibkr_realized.get((sym, ccy)),
            n_sells=n_sells.get((sym, ccy), 0),
        )
        for sym, ccy in sorted(keys)
    ]

    if incomplete:
        logger.info(
            "Reconciliation: %d symbol(s) skipped (IBKR realized P/L elided): %s",
            len(incomplete),
            ", ".join(sorted(f"{s} ({c})" for s, c in incomplete)),
        )
    return results
