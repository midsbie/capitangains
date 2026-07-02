"""Unit tests for transfer-basis resolution: the index, its guards, the in-place fill.

The basis of a transferred-in lot lives in the Open Positions 'Cost Basis' column of the
statement covering the transfer, not the transfer-day Market Value. TransferBasisIndex
pins a transfer to its own snapshot by three guards: period-containment (the transfer's
own year), quantity match (rejecting a snapshot blended by same-year activity), and
no-trading (rejecting a snapshot whose period saw same-symbol trading that a buy/sell
round trip may have blended off the transferred lot's basis while netting its quantity).
"""

import datetime as dt
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting import (
    OpenPositionRow,
    StatementInput,
    StatementMetadata,
    StatementPeriod,
    TransferBasisIndex,
    build_transfer_basis_index,
    resolve_transfer_basis,
)
from tests.support import (
    OPEN_POSITIONS_COLUMNS,
    header_row,
    open_position_data,
    parse_model,
    trade_row,
    transfer_row,
)

_GBP = Currency("GBP")


def _period(year: int) -> StatementPeriod:
    return StatementPeriod(dt.date(year, 1, 1), dt.date(year, 12, 31))


def test_resolve_selects_transfer_year_snapshot_over_a_later_blended_one():
    # AZN: transferred in 2021 (qty 10) and added to by 2024 (the blended qty-56
    # snapshot). Period-containment anchors to 2021, and the quantity guard would reject
    # the 2024 snapshot regardless, so the resolved basis is the 2021 cost, not 5940.54.
    index = TransferBasisIndex()
    index.add(
        _period(2021), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )
    index.add(
        _period(2024), OpenPositionRow("AZN", _GBP, Decimal("56"), Decimal("5940.54"))
    )

    transfer = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="10",
        market_value="881.40",
    )
    assert index.resolve(transfer) == Decimal("708.495")


def test_resolve_returns_none_on_quantity_mismatch():
    # A transfer dated within the snapshot's period but whose quantity differs (a
    # same-year purchase or partial sale blended it): the snapshot is not this lot's.
    index = TransferBasisIndex()
    index.add(
        _period(2021), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )

    transfer = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="12",
        market_value="881.40",
    )
    assert index.resolve(transfer) is None


def test_resolve_returns_none_when_period_saw_same_symbol_trading():
    # The snapshot quantity still matches the transfer (a same-period buy and an
    # offsetting sale netted it back to 10), but its basis may now be blended. The
    # no-trading guard refuses the snapshot, so the lot falls through to unresolved
    # rather than seeding FIFO from a basis a round trip may have corrupted.
    index = TransferBasisIndex()
    index.add(
        _period(2021), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )
    index.mark_trade_activity(
        [trade_row(symbol="AZN", currency="GBP", date="2021-06-01")]
    )

    transfer = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="10",
        market_value="881.40",
    )
    assert index.resolve(transfer) is None


def test_mark_trade_activity_does_not_disqualify_a_different_period():
    # Trading in a later period must not poison the transfer-year snapshot: the guard is
    # keyed by (symbol, currency, period), and the 2024 trade lands only in 2024.
    index = TransferBasisIndex()
    index.add(
        _period(2021), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )
    index.add(
        _period(2024), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )
    index.mark_trade_activity(
        [trade_row(symbol="AZN", currency="GBP", date="2024-03-01")]
    )

    transfer = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="10",
        market_value="881.40",
    )
    assert index.resolve(transfer) == Decimal("708.495")


def test_resolve_returns_none_for_absent_symbol():
    index = TransferBasisIndex()
    index.add(
        _period(2021), OpenPositionRow("AZN", _GBP, Decimal("10"), Decimal("708.495"))
    )

    transfer = transfer_row(
        symbol="LLOY",
        currency="GBP",
        date="2021-10-15",
        quantity="2500",
        market_value="1211.00",
    )
    assert index.resolve(transfer) is None


def test_build_index_and_resolve_fills_cost_basis_in_place():
    # Build from two per-file models (as the pipeline does before merge_models erases
    # the per-statement period), then fill each transfer's basis in place.
    model_2021 = parse_model(
        [header_row("Open Positions", OPEN_POSITIONS_COLUMNS), open_position_data()]
    )
    model_2024 = parse_model(
        [
            header_row("Open Positions", OPEN_POSITIONS_COLUMNS),
            open_position_data(
                quantity="56",
                cost_price="106.081071429",
                cost_basis="5940.54",
                close_price="104.68",
                value="5862.08",
                unrealized_pl="-78.46",
            ),
        ]
    )
    statements = [
        StatementInput("2021.csv", StatementMetadata("U1", _period(2021))),
        StatementInput("2024.csv", StatementMetadata("U1", _period(2024))),
    ]

    index, defects = build_transfer_basis_index(
        statements, {"2021.csv": model_2021, "2024.csv": model_2024}
    )
    assert defects == []

    resolved = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="10",
        market_value="881.40",
    )
    unresolved = transfer_row(
        symbol="AZN",
        currency="GBP",
        date="2021-10-15",
        quantity="12",
        market_value="900",
    )
    resolve_transfer_basis([resolved, unresolved], index)

    assert resolved.cost_basis == Decimal(
        "708.495"
    )  # 2021 snapshot, not 2024's 5940.54
    assert unresolved.cost_basis is None  # blended qty matches no snapshot
