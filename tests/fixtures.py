"""Test fixtures for trade and transfer objects.

Production code parses trades/transfers from CSV files via extract.py into
TradeRow/TransferRow dataclasses. It never constructs them manually.

Tests need lightweight objects that satisfy TradeProtocol/TransferProtocol
without the full CSV parsing machinery. These dataclasses serve that purpose.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Trade:
    """Test fixture implementing TradeProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    proceeds: Decimal
    comm_fee: Decimal
    basis_ccy: Decimal | None = None


@dataclass
class Transfer:
    """Test fixture implementing TransferProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: str
    direction: str
    market_value: Decimal
    code: str = ""
