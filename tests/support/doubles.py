"""Minimal duck-typed doubles for trade/transfer objects.

Two distinct kinds of test input coexist in this package, and they are not
interchangeable:

- The doubles here (``Trade``, ``Transfer``) carry only the fields named by
  ``TradeProtocol`` / ``TransferProtocol``. They exist to prove that the FIFO
  matcher and the gap policies depend on nothing more than the protocol surface;
  reach for them only when that minimalism is the point of the test.
- The full-shape ``TradeRow`` / ``TransferRow`` built by ``builders.trade_row`` /
  ``builders.transfer_row`` are what every other test wants: the real production
  dataclasses, with the section/datetime/code/basis fields the extractors and
  report sink read.

Production code never constructs these doubles; it parses TradeRow/TransferRow
from CSV via the extract layer. The doubles are a test-only convenience.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from capitangains.conv import Currency


@dataclass
class Trade:
    """Test fixture implementing TradeProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: Currency
    proceeds: Decimal
    comm_fee: Decimal
    basis_ccy: Decimal | None = None


@dataclass
class Transfer:
    """Test fixture implementing TransferProtocol."""

    date: dt.date
    symbol: str
    quantity: Decimal
    currency: Currency
    direction: str
    market_value: Decimal
    code: str = ""
