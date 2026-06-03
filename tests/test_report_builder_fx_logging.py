"""Per-occurrence FX diagnostics from ReportBuilder.convert_eur.

Each missing/fallback rate is surfaced at INFO so a -v run shows exactly which figures
were left unconverted or basis-approximated. The aggregate WARNING lives in the CLI.
"""

import datetime as dt
import logging
from decimal import Decimal

from capitangains.reporting.extract import DividendRow
from capitangains.reporting.fifo_domain import RealizedLine, SellMatchLeg
from capitangains.reporting.fx import FxTable
from capitangains.reporting.report_builder import ReportBuilder

_RB_LOGGER = "capitangains.reporting.report_builder"


def _make_fx(rates):
    table = FxTable()
    for (ccy, date), value in rates.items():
        table.data[ccy][date] = value
    for ccy, m in table.data.items():
        table.date_index[ccy] = sorted(m.keys())
    return table


def _realized(currency, sell_date, buy_date):
    leg = SellMatchLeg(
        buy_date=buy_date,
        qty=Decimal("10"),
        lot_qty_before=Decimal("10"),
        alloc_cost_ccy=Decimal("900"),
    )
    return RealizedLine(
        symbol="AAPL",
        currency=currency,
        sell_date=sell_date,
        sell_qty=Decimal("10"),
        sell_gross_ccy=Decimal("100"),
        sell_comm_ccy=Decimal("0"),
        sell_net_ccy=Decimal("100"),
        legs=[leg],
        realized_pl_ccy=Decimal("100") - Decimal("900"),
    )


def test_missing_sell_rate_logs_info_and_marks_fx_missing(caplog):
    rb = ReportBuilder(year=2024)
    rb.add_realized(_realized("USD", dt.date(2024, 6, 10), dt.date(2023, 1, 1)))
    fx = _make_fx({})  # no USD rates at all

    with caplog.at_level(logging.INFO, logger=_RB_LOGGER):
        rb.convert_eur(fx)

    assert rb.fx_missing is True
    assert any(
        "Sell FX rate missing for USD on 2024-06-10" in r.getMessage()
        for r in caplog.records
    )


def test_missing_buy_rate_falls_back_to_sell_rate_with_info(caplog):
    rb = ReportBuilder(year=2024)
    rb.add_realized(_realized("USD", dt.date(2024, 6, 10), dt.date(2023, 1, 1)))
    # Sell-date rate present; buy-date (and any earlier) rate absent.
    fx = _make_fx({("USD", "2024-06-10"): Decimal("0.9")})

    with caplog.at_level(logging.INFO, logger=_RB_LOGGER):
        rb.convert_eur(fx)

    assert any(
        "Buy-date FX rate missing for USD on 2023-01-01" in r.getMessage()
        for r in caplog.records
    )


def test_missing_amount_rate_logs_info_and_marks_fx_missing(caplog):
    rb = ReportBuilder(year=2024)
    rb.set_dividends(
        [
            DividendRow(
                currency="USD",
                date=dt.date(2024, 6, 15),
                description="d",
                amount=Decimal("10"),
            )
        ]
    )
    # Only a later USD rate exists, so no fallback is available for the dividend date.
    fx = _make_fx({("USD", "2024-09-01"): Decimal("0.9")})

    with caplog.at_level(logging.INFO, logger=_RB_LOGGER):
        rb.convert_eur(fx)

    assert rb.fx_missing is True
    assert any(
        "FX rate missing for USD on 2024-06-15" in r.getMessage()
        and "left unconverted" in r.getMessage()
        for r in caplog.records
    )
