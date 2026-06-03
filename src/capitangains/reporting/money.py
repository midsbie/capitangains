from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MoneyLike = str | Decimal

_MONEY_Q = Decimal("0.01")
_ALLOCATION_Q = Decimal("0.00000001")

# Single source of truth for the project's monetary rounding policy. Rounding is passed
# explicitly at every quantization point rather than via the global decimal context, so
# results are independent of import order, thread, and any external consumer's context.
_ROUNDING = ROUND_HALF_UP


def quantize_money(value: Decimal, places: MoneyLike = _MONEY_Q) -> Decimal:
    """Quantize monetary values consistently across the codebase."""
    quant = Decimal(places)
    return value.quantize(quant, rounding=_ROUNDING)


def quantize_allocation(value: Decimal) -> Decimal:
    """Quantize allocation amounts (e.g., proportional basis)."""
    return value.quantize(_ALLOCATION_Q, rounding=_ROUNDING)


def round_cost_piece(total_basis: Decimal, take: Decimal, lot_qty: Decimal) -> Decimal:
    """Allocate a proportional amount of basis with deterministic rounding."""
    if lot_qty == 0:
        return Decimal("0")
    ratio = take / lot_qty
    alloc = total_basis * ratio
    return quantize_allocation(alloc)


def abs_decimal(value: Decimal) -> Decimal:
    """Return the absolute value using Decimal.copy_abs for stability."""
    return value.copy_abs()
