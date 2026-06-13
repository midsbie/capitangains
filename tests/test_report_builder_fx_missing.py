"""ReportBuilder.convert_eur accumulates every unresolved (date, currency).

A missing rate is never substituted with another date's rate; the affected
line/amount is left unconverted and the gap is recorded in ``fx_missing`` for the CLI
to abort on.
"""

import datetime as dt
from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import DividendRow
from capitangains.reporting.report_builder import ReportBuilder
from tests.support import make_fx, realized_line, sell_match_leg


def _realized(currency, sell_date, buy_date):
    return realized_line(
        currency=currency,
        sell_date=sell_date,
        legs=[
            sell_match_leg(
                buy_date=buy_date,
                qty="10",
                lot_qty_before="10",
                alloc_cost_ccy="900",
            )
        ],
    )


def test_missing_sell_rate_records_gap_and_leaves_line_unconverted():
    rb = ReportBuilder(year=2024)
    rl = _realized("USD", dt.date(2024, 6, 10), dt.date(2023, 1, 1))
    rb.add_realized(rl)

    rb.convert_eur(make_fx({}))  # no USD rates at all

    assert (dt.date(2024, 6, 10), Currency("USD")) in rb.fx_missing
    assert rl.sell_net_eur is None
    assert rl.realized_pl_eur is None


def test_missing_buy_rate_records_gap_without_substituting_sell_rate():
    rb = ReportBuilder(year=2024)
    rl = _realized("USD", dt.date(2024, 6, 10), dt.date(2023, 1, 1))
    rb.add_realized(rl)

    # Sell-date rate present; buy-date (and any earlier) rate absent.
    rb.convert_eur(make_fx({("USD", "2024-06-10"): Decimal("0.9")}))

    # The buy-date gap is recorded and the whole line is left unconverted -- the
    # sell-date rate is never substituted for the missing acquisition rate.
    assert rb.fx_missing == {(dt.date(2023, 1, 1), Currency("USD"))}
    assert rl.legs[0].alloc_cost_eur is None
    assert rl.alloc_cost_eur is None
    assert rl.realized_pl_eur is None


def test_missing_amount_rate_records_gap_and_leaves_amount_unconverted():
    rb = ReportBuilder(year=2024)
    div = DividendRow(
        currency=Currency("USD"),
        date=dt.date(2024, 6, 15),
        description="d",
        amount=Decimal("10"),
    )
    rb.set_dividends([div])

    # Only a later USD rate exists, so no fallback is available for the dividend date.
    rb.convert_eur(make_fx({("USD", "2024-09-01"): Decimal("0.9")}))

    assert (dt.date(2024, 6, 15), Currency("USD")) in rb.fx_missing
    assert div.amount_eur is None


def test_complete_table_records_no_gap_and_converts_each_leg_at_its_own_date():
    rb = ReportBuilder(year=2024)
    rl = _realized("USD", dt.date(2024, 6, 10), dt.date(2023, 1, 1))
    rb.add_realized(rl)

    rb.convert_eur(
        make_fx(
            {
                ("USD", "2024-06-10"): Decimal("0.9"),  # sell-date rate
                ("USD", "2023-01-01"): Decimal("1.1"),  # buy-date rate
            }
        )
    )

    assert not rb.fx_missing
    # Cost basis at the buy-date rate (1.1 gives 990), proceeds at the sell-date rate
    # (0.9 gives 90); the sell rate is not applied to the acquisition leg.
    assert rl.legs[0].alloc_cost_eur == Decimal("990.00")
    assert rl.sell_net_eur == Decimal("90.00")
