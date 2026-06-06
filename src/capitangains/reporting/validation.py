"""Cross-row data-quality invariants over extracted statement events.

These checks are pure domain logic: they inspect already-extracted rows and raise
``DataQualityError`` on a violation, with no logging or process-exit side effects. The
CLI boundary is responsible for translating the raised error into a user-facing message
and exit code.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from capitangains.errors import DataQualityError

from .extract import TradeRow, TransferRow


def validate_symbol_currency_uniqueness(
    trades: Sequence[TradeRow], transfers: Sequence[TransferRow]
) -> None:
    """Enforce one-currency-per-symbol invariant across all extracted events.

    Design choice: IBKR symbols are treated as exchange-specific identifiers, each
    denominated in a single currency.  If the same ticker appears on exchanges with
    different currencies (e.g. "RY" on NYSE/USD and TSX/CAD), the CSV data must
    disambiguate them with distinct symbols.  Allowing multiple currencies per symbol
    would make the per-symbol summary incoherent -- trade-currency columns can only
    represent one denomination, while EUR columns aggregate across all, producing
    rows that cannot be reconciled.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    events: Sequence[TradeRow | TransferRow] = [*trades, *transfers]
    for event in events:
        seen[event.symbol].add(event.currency)
    violations = {sym: ccys for sym, ccys in seen.items() if len(ccys) > 1}
    if not violations:
        return
    details = "\n".join(
        f"  {sym}: {', '.join(sorted(ccys))}"
        for sym, ccys in sorted(violations.items())
    )
    raise DataQualityError(
        f"symbol-currency uniqueness violated -- each symbol must map to exactly "
        f"one trade currency, but the following appear in multiple:\n{details}"
    )
