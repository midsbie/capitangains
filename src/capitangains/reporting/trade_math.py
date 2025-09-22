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
