"""Per-trade monetary arithmetic, in the trade's own currency.

All figures derive from IBKR's signed Proceeds (the booked total consideration of the
fill) and Comm/Fee, which is deliberately never from unit price (T. Price, captured on
TradeRow for audit but unused here). Tax basis and realization value are functions of
total consideration, not unit price, and Proceeds is the authoritative booked total;
reconstructing it as Quantity × T. Price would only reintroduce per-unit rounding scaled
by quantity. Basis includes buy commission; net proceeds deduct sell commission, which
matches PT treatment (acquisition/disposal value adjusted by the expenses inherent to
each).

"""

from __future__ import annotations

from decimal import Decimal

from .money import abs_decimal


def buy_cost_ccy(proceeds: Decimal, comm_fee: Decimal) -> Decimal:
    """Buy cash outflow = -proceeds - comm_fee."""
    return (-proceeds) - comm_fee


def sell_gross_ccy(proceeds: Decimal) -> Decimal:
    """Sell gross cash inflow (before fees)."""
    return abs_decimal(proceeds)


def sell_net_ccy(proceeds: Decimal, comm_fee: Decimal) -> Decimal:
    """Net proceeds after fees."""
    return proceeds + comm_fee
