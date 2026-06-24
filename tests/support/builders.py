"""Domain-object layer: build the real production dataclasses tests assert against.

One canonical builder per concept (parse_model, trade_row, transfer_row, buy, sell,
realized_line, sell_match_leg, convert, make_fx, make_matcher, ingest, make_gap_event).
Numeric params accept ``Decimal | str`` and dates accept ``dt.date | str``; both coerce
internally so call sites stay terse. Defaults are chosen so the single builder subsumes
every legacy per-file variant, and a call site overrides only what it cares about.

For minimal protocol doubles (not the full TradeRow/TransferRow), see doubles.py.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from capitangains.conv import Currency
from capitangains.model import IbkrModel, IbkrStatementCsvParser
from capitangains.reporting.converted import ConvertedRealizedLine
from capitangains.reporting.extract import TradeRow, TransferRow
from capitangains.reporting.fifo import FifoMatcher
from capitangains.reporting.fifo_domain import (
    GapEvent,
    GapKey,
    GapResolution,
    RealizedLine,
    SellMatchLeg,
    TradeProtocol,
)
from capitangains.reporting.fx import FxTable
from capitangains.reporting.gap_policy import build_gap_policy
from capitangains.reporting.report_builder import ReportBuilder

LegSpec = SellMatchLeg | Mapping[str, Any]


def _dec(value: Decimal | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


def _opt_dec(value: Decimal | str | None) -> Decimal | None:
    return None if value is None else _dec(value)


def _date(value: dt.date | str) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


def _ccy(value: Currency | str) -> Currency:
    return value if isinstance(value, Currency) else Currency(value)


def parse_model(rows: Iterable[Any]) -> IbkrModel:
    """Parse raw CSV rows into an IbkrModel, discarding the ParseReport."""
    model, _ = IbkrStatementCsvParser().parse_rows(rows)
    return model


def trade_row(
    *,
    symbol: str = "AAPL",
    currency: Currency | str = "USD",
    asset_category: str = "Stocks",
    date: dt.date | str = dt.date(2024, 1, 1),
    datetime_str: str | None = None,
    quantity: Decimal | str = "10",
    t_price: Decimal | str = "100",
    proceeds: Decimal | str | None = None,
    comm_fee: Decimal | str = "-1",
    code: str = "O",
    basis_ccy: Decimal | str | None = None,
    realized_pl_ccy: Decimal | str | None = None,
) -> TradeRow:
    """Build a full TradeRow. ``proceeds`` defaults to the buy/sell sign convention."""
    d = _date(date)
    qty = _dec(quantity)
    if datetime_str is None:
        datetime_str = f"{d.isoformat()}, 10:00:00"
    if proceeds is None:
        proceeds = Decimal("-1000") if qty > 0 else Decimal("1000")
    return TradeRow(
        section="Trades",
        asset_category=asset_category,
        currency=_ccy(currency),
        symbol=symbol,
        datetime_str=datetime_str,
        date=d,
        quantity=qty,
        t_price=_dec(t_price),
        proceeds=_dec(proceeds),
        comm_fee=_dec(comm_fee),
        code=code,
        basis_ccy=_opt_dec(basis_ccy),
        realized_pl_ccy=_opt_dec(realized_pl_ccy),
    )


def buy(
    symbol: str,
    date: dt.date | str,
    qty: Decimal | str,
    proceeds: Decimal | str,
    comm: Decimal | str,
    *,
    ccy: Currency | str = "USD",
) -> TradeRow:
    """A buy TradeRow: proceeds negative (cash out), comm negative."""
    return trade_row(
        symbol=symbol,
        currency=ccy,
        date=date,
        datetime_str=_date(date).isoformat(),
        quantity=qty,
        t_price="0",
        proceeds=proceeds,
        comm_fee=comm,
        code="P",
    )


def sell(
    symbol: str,
    date: dt.date | str,
    qty: Decimal | str,
    proceeds: Decimal | str,
    comm: Decimal | str,
    *,
    basis: Decimal | str | None = None,
    realized: Decimal | str | None = None,
    ccy: Currency | str = "USD",
) -> TradeRow:
    """A sell TradeRow: proceeds positive, comm negative; ``basis`` feeds gap synthesis,
    ``realized`` is IBKR's per-trade Realized P/L (the reconciler's RHS)."""
    return trade_row(
        symbol=symbol,
        currency=ccy,
        date=date,
        datetime_str=_date(date).isoformat(),
        quantity=qty,
        t_price="0",
        proceeds=proceeds,
        comm_fee=comm,
        code="P",
        basis_ccy=basis,
        realized_pl_ccy=realized,
    )


def transfer_row(
    *,
    symbol: str = "AAPL",
    currency: Currency | str = "USD",
    asset_category: str = "Stocks",
    date: dt.date | str = dt.date(2024, 1, 1),
    direction: str = "In",
    quantity: Decimal | str = "100",
    market_value: Decimal | str = "10000",
    code: str = "",
) -> TransferRow:
    """Build a full TransferRow."""
    return TransferRow(
        section="Transfers",
        asset_category=asset_category,
        currency=_ccy(currency),
        symbol=symbol,
        date=_date(date),
        direction=direction,
        quantity=_dec(quantity),
        market_value=_dec(market_value),
        code=code,
    )


def sell_match_leg(
    *,
    buy_date: dt.date | str | None = None,
    qty: Decimal | str = "0",
    alloc_cost_ccy: Decimal | str = "0",
    synthetic: bool = False,
    transferred: bool = False,
) -> SellMatchLeg:
    """Build a SellMatchLeg, centralizing the leg literal repeated across the suite."""
    return SellMatchLeg(
        buy_date=None if buy_date is None else _date(buy_date),
        qty=_dec(qty),
        alloc_cost_ccy=_dec(alloc_cost_ccy),
        synthetic=synthetic,
        transferred=transferred,
    )


def _leg_from_spec(spec: Mapping[str, Any]) -> SellMatchLeg:
    qty = spec["qty"]
    return sell_match_leg(
        buy_date=spec.get("buy_date"),
        qty=qty,
        alloc_cost_ccy=spec["alloc_cost_ccy"],
        synthetic=spec.get("synthetic", False),
        transferred=spec.get("transferred", False),
    )


def _normalize_legs(legs: Iterable[LegSpec] | None) -> list[SellMatchLeg]:
    if legs is None:
        return []
    return [
        leg if isinstance(leg, SellMatchLeg) else _leg_from_spec(leg) for leg in legs
    ]


def realized_line(
    *,
    symbol: str = "AAPL",
    currency: Currency | str = "USD",
    sell_date: dt.date | str = dt.date(2024, 1, 1),
    legs: Iterable[LegSpec] | None = None,
    sell_gross_ccy: Decimal | str = "100",
    sell_comm_ccy: Decimal | str = "0",
    sell_net_ccy: Decimal | str | None = None,
    sell_qty: Decimal | str | None = None,
    has_gap: bool = False,
    gap_fixed: bool = False,
    ibkr_realized_elided: bool = False,
) -> RealizedLine:
    """Build a RealizedLine. Derived defaults: sell_net == sell_gross; sell_qty == sum
    of leg qtys. Allocated cost and realized P/L are RealizedLine properties (cost from
    the legs, P/L == sell_net minus cost), so a caller shapes the P/L through the legs
    and sell_net, never by setting it directly."""
    leg_objs = _normalize_legs(legs)
    gross = _dec(sell_gross_ccy)
    net = gross if sell_net_ccy is None else _dec(sell_net_ccy)
    qty = (
        sum((leg.qty for leg in leg_objs), Decimal("0"))
        if sell_qty is None
        else _dec(sell_qty)
    )
    return RealizedLine(
        symbol=symbol,
        currency=_ccy(currency),
        sell_date=_date(sell_date),
        sell_qty=qty,
        sell_gross_ccy=gross,
        sell_comm_ccy=_dec(sell_comm_ccy),
        sell_net_ccy=net,
        legs=leg_objs,
        has_gap=has_gap,
        gap_fixed=gap_fixed,
        ibkr_realized_elided=ibkr_realized_elided,
    )


def convert(rl: RealizedLine, fx: FxTable | None = None) -> ConvertedRealizedLine:
    """Run one realized line through ReportBuilder.convert_eur and return its EUR view.

    The default fx=None is the EUR-native path (every rate is 1); pass an FxTable for a
    non-EUR line. The line must be fully convertible (every required rate present): a
    convert_eur that drops an unconvertible line would leave converted_lines empty.
    """
    rb = ReportBuilder(year=rl.sell_date.year)
    rb.add_realized(rl)
    rb.convert_eur(fx)
    (converted,) = rb.converted_lines
    return converted


def make_fx(rates: Mapping[tuple[str, str], Decimal]) -> FxTable:
    """Build an FxTable from ``{(currency, "yyyy-mm-dd"): eur_per_unit}``."""
    table = FxTable()
    for (ccy, date), value in rates.items():
        table.data[_ccy(ccy)][_date(date)] = value
    for cur, by_date in table.data.items():
        table.date_index[cur] = sorted(by_date.keys())
    return table


def make_matcher(acknowledged: frozenset[GapKey] = frozenset()) -> FifoMatcher:
    """A matcher whose gap policy acknowledges exactly ``acknowledged``."""
    return FifoMatcher(gap_policy=build_gap_policy(acknowledged))


def ingest(matcher: FifoMatcher, trades: Iterable[TradeProtocol]) -> list[RealizedLine]:
    """Drive trades through the matcher, returning one RealizedLine per sell."""
    return [rl for t in trades if (rl := matcher.ingest_trade(t)) is not None]


def make_gap_event(
    *,
    symbol: str = "AAPL",
    date: dt.date | str = dt.date(2024, 1, 1),
    remaining_qty: Decimal | str = "1",
    currency: Currency | str = "USD",
    message: str = "test",
    outcome: GapResolution,
) -> GapEvent:
    """Build a GapEvent; ``outcome`` is required."""
    return GapEvent(
        symbol=symbol,
        date=_date(date),
        remaining_qty=_dec(remaining_qty),
        currency=_ccy(currency),
        message=message,
        outcome=outcome,
    )
