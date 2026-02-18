import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook

from capitangains.cmd.cli import validate_symbol_currency_uniqueness
from capitangains.reporting.extract import (
    DividendRow,
    InterestRow,
    SyepInterestRow,
    TradeRow,
    WithholdingRow,
)
from capitangains.reporting.fifo_domain import RealizedLine, SellMatchLeg
from capitangains.reporting.fx import FxTable
from capitangains.reporting.report_builder import ReportBuilder
from capitangains.reporting.report_sink import ExcelReportSink


def _make_fx(rates):
    table = FxTable()
    for (ccy, date), value in rates.items():
        table.data[ccy][date] = value
    for ccy, m in table.data.items():
        table.date_index[ccy] = sorted(m.keys())
    return table


def _realized(
    symbol: str, currency: str, sell_date: dt.date, legs: list[dict[str, Any]]
):
    leg_objs = [
        SellMatchLeg(
            buy_date=leg["buy_date"],
            qty=leg["qty"],
            lot_qty_before=leg.get("lot_qty_before", leg["qty"]),
            alloc_cost_ccy=leg["alloc_cost_ccy"],
        )
        for leg in legs
    ]
    sell_qty = sum((leg.qty for leg in leg_objs), Decimal("0"))
    sell_net = Decimal("100")
    sell_gross = sell_net
    return RealizedLine(
        symbol=symbol,
        currency=currency,
        sell_date=sell_date,
        sell_qty=sell_qty,
        sell_gross_ccy=sell_gross,
        sell_comm_ccy=Decimal("0"),
        sell_net_ccy=sell_net,
        legs=leg_objs,
        realized_pl_ccy=sell_net
        - sum((leg.alloc_cost_ccy for leg in leg_objs), Decimal("0")),
    )


def test_report_builder_add_realized_accumulates_symbol_totals():
    rb = ReportBuilder(year=2024)
    legs = [
        {
            "buy_date": dt.date(2023, 1, 1),
            "qty": Decimal("5"),
            "alloc_cost_ccy": Decimal("40"),
        }
    ]
    rl1 = _realized("ABC", "USD", dt.date(2024, 1, 1), legs)
    rl2 = _realized("ABC", "USD", dt.date(2024, 2, 1), legs)
    rb.add_realized(rl1)
    rb.add_realized(rl2)

    totals = rb.symbol_totals["ABC"]
    usd = totals.by_currency["USD"]
    assert usd.realized == rl1.realized_pl_ccy + rl2.realized_pl_ccy
    assert usd.proceeds == rl1.sell_net_ccy + rl2.sell_net_ccy


def _trade_row(symbol: str, currency: str) -> TradeRow:
    return TradeRow(
        section="Trades",
        asset_category="Stocks",
        currency=currency,
        symbol=symbol,
        datetime_str="2024-01-01, 10:00:00",
        date=dt.date(2024, 1, 1),
        quantity=Decimal("10"),
        t_price=Decimal("100"),
        proceeds=Decimal("-1000"),
        comm_fee=Decimal("-1"),
        code="O",
    )


def test_multi_currency_same_symbol_rejected():
    """Same symbol in multiple currencies must raise at validation time."""
    trades = [_trade_row("ABC", "USD"), _trade_row("ABC", "EUR")]
    with pytest.raises(ValueError, match="symbol-currency uniqueness"):
        validate_symbol_currency_uniqueness(trades, [])


def test_report_builder_convert_eur_handles_missing_fx_and_leg_fallback():
    rb = ReportBuilder(year=2024)
    usd_legs: list[dict[str, Any]] = [
        {
            "buy_date": dt.date(2023, 6, 1),
            "qty": Decimal("5"),
            "alloc_cost_ccy": Decimal("40"),
        },
        {
            "buy_date": None,
            "qty": Decimal("5"),
            "alloc_cost_ccy": Decimal("50"),
        },
    ]
    rl_usd = _realized("USD1", "USD", dt.date(2024, 3, 1), usd_legs)
    gbp_legs = [
        {
            "buy_date": dt.date(2024, 2, 1),
            "qty": Decimal("2"),
            "alloc_cost_ccy": Decimal("20"),
        }
    ]
    rl_gbp = _realized("GBP1", "GBP", dt.date(2024, 1, 10), gbp_legs)
    rb.add_realized(rl_usd)
    rb.add_realized(rl_gbp)

    fx = _make_fx(
        {
            ("USD", "2024-03-01"): Decimal("0.9"),
        }
    )

    rb.convert_eur(fx)

    # USD trade should convert using fallback rate for missing buy date leg
    allocs = [leg.alloc_cost_eur for leg in rl_usd.legs]
    assert all(val is not None for val in allocs)
    assert rb.fx_missing is True  # GBP missing rate sets this flag

    # Zero sell quantity should skip proceeds share allocation gracefully
    zero_qty_rl = RealizedLine(
        symbol="ZQ",
        currency="USD",
        sell_date=dt.date(2024, 4, 1),
        sell_qty=Decimal("0"),
        sell_gross_ccy=Decimal("0"),
        sell_comm_ccy=Decimal("0"),
        sell_net_ccy=Decimal("0"),
        legs=[
            SellMatchLeg(
                buy_date=None,
                qty=Decimal("0"),
                lot_qty_before=Decimal("0"),
                alloc_cost_ccy=Decimal("0"),
            )
        ],
        realized_pl_ccy=Decimal("0"),
    )
    rb.add_realized(zero_qty_rl)
    rb.convert_eur(fx)
    assert zero_qty_rl.legs[0].proceeds_share_eur is None


def test_report_builder_income_conversion():
    rb = ReportBuilder(year=2024)
    rb.set_dividends(
        [
            DividendRow(
                currency="USD",
                date=dt.date(2024, 1, 1),
                description="Div USD",
                amount=Decimal("10"),
            ),
            DividendRow(
                currency="EUR",
                date=dt.date(2024, 1, 2),
                description="Div EUR",
                amount=Decimal("5"),
            ),
        ]
    )
    rb.set_withholding(
        [
            WithholdingRow(
                currency="USD",
                date=dt.date(2024, 1, 1),
                description="Tax",
                amount=Decimal("-2"),
                code="",
                type="",
                country="",
            ),
        ]
    )
    rb.set_syep_interest(
        [
            SyepInterestRow(
                currency="USD",
                value_date=dt.date(2024, 1, 1),
                symbol="SYEP",
                start_date=None,
                quantity=Decimal("-1"),
                collateral_amount=Decimal("0"),
                market_rate_pct=Decimal("0"),
                customer_rate_pct=Decimal("0"),
                interest_paid=Decimal("1"),
                code="",
            )
        ]
    )

    fx = _make_fx({("USD", "2024-01-01"): Decimal("0.9")})
    rb.convert_eur(fx)

    assert rb.dividends[0].amount_eur == Decimal("9.00")
    assert rb.dividends[1].amount_eur == Decimal("5.00")
    assert rb.withholding[0].amount_eur == Decimal("-1.80")
    assert rb.syep_interest[0].interest_paid_eur == Decimal("0.90")


def test_excel_report_sink_handles_empty_report(tmp_path):
    rb = ReportBuilder(year=2024)
    out_path = tmp_path / "report.xlsx"
    sink = ExcelReportSink(out_path=out_path, locale="EN")
    sink.write(rb)

    wb = load_workbook(out_path)
    assert set(wb.sheetnames) >= {
        "Trading Totals",
        "Realized Trades",
        "Per Symbol Summary",
    }


def test_excel_report_sink_serializes_legs(tmp_path):
    rb = ReportBuilder(year=2024)
    leg = {
        "buy_date": dt.date(2023, 1, 1),
        "qty": Decimal("5"),
        "alloc_cost_ccy": Decimal("40"),
    }
    rl = _realized("ABC", "USD", dt.date(2024, 1, 1), [leg])
    rb.add_realized(rl)
    rb.convert_eur(_make_fx({("USD", "2024-01-01"): Decimal("0.9")}))

    out_path = tmp_path / "report_with_leg.xlsx"
    sink = ExcelReportSink(out_path=out_path, locale="EN")
    sink.write(rb)

    wb = load_workbook(out_path)
    ws = wb["Realized Trades"]
    legs_json = ws.cell(row=2, column=15).value
    assert isinstance(legs_json, str) and '"buy_date": "2023-01-01"' in legs_json


def test_excel_report_sink_sorts_dividends_by_description(tmp_path):
    rb = ReportBuilder(year=2024)
    rb.set_dividends(
        [
            DividendRow(
                currency="USD",
                date=dt.date(2024, 1, 2),
                description="Zulu",
                amount=Decimal("2"),
            ),
            DividendRow(
                currency="USD",
                date=dt.date(2024, 1, 1),
                description="Alpha",
                amount=Decimal("1"),
            ),
        ]
    )

    out_path = tmp_path / "dividends_sorted.xlsx"
    sink = ExcelReportSink(out_path=out_path, locale="EN")
    sink.write(rb)

    wb = load_workbook(out_path)
    ws = wb["Dividends"]
    descriptions = [ws.cell(row=i, column=3).value for i in range(2, ws.max_row + 1)]
    descriptions_str = [str(d) for d in descriptions]
    assert descriptions_str == sorted(descriptions_str)


def test_excel_report_sink_sorts_account_interest(tmp_path):
    rb = ReportBuilder(year=2024)
    rb.set_interest(
        [
            InterestRow(
                currency="USD",
                date=dt.date(2024, 1, 2),
                description="Zulu",
                amount=Decimal("2"),
            ),
            InterestRow(
                currency="USD",
                date=dt.date(2024, 1, 1),
                description="Alpha",
                amount=Decimal("1"),
            ),
        ]
    )

    out_path = tmp_path / "interest_sorted.xlsx"
    sink = ExcelReportSink(out_path=out_path, locale="EN")
    sink.write(rb)

    wb = load_workbook(out_path)
    ws = wb["Account Interest"]
    descriptions = [ws.cell(row=i, column=3).value for i in range(2, ws.max_row + 1)]
    descriptions_str = [str(d) for d in descriptions]
    assert descriptions_str == sorted(descriptions_str)


@pytest.mark.parametrize(
    "currency, fx_rates",
    [
        ("EUR", None),
        ("USD", {("USD", "2024-06-15"): Decimal("0.9")}),
    ],
    ids=["eur_native", "fx_converted"],
)
def test_proceeds_allocation_sums_to_sell_net_eur(currency, fx_rates):
    """Proceeds split across 3 equal legs must sum exactly to sell_net_eur."""
    legs = [
        {
            "buy_date": dt.date(2023, 1, i),
            "qty": Decimal("10"),
            "alloc_cost_ccy": Decimal("30"),
        }
        for i in range(1, 4)
    ]
    rl = _realized("XYZ", currency, dt.date(2024, 6, 15), legs)
    # sell_net_ccy = 100 (from _realized helper); 100 / 3 is non-terminating

    rb = ReportBuilder(year=2024)
    rb.add_realized(rl)
    fx = _make_fx(fx_rates) if fx_rates else None
    rb.convert_eur(fx)

    shares = [leg.proceeds_share_eur for leg in rl.legs]
    assert all(s is not None for s in shares)
    assert sum(shares) == rl.sell_net_eur


def test_excel_report_sink_sorts_withholding(tmp_path):
    rb = ReportBuilder(year=2024)
    rb.set_withholding(
        [
            WithholdingRow(
                currency="USD",
                date=dt.date(2024, 1, 3),
                description="Bravo",
                amount=Decimal("-2"),
                code="",
                type="",
                country="",
            ),
            WithholdingRow(
                currency="EUR",
                date=dt.date(2024, 1, 1),
                description="Zulu",
                amount=Decimal("-1"),
                code="",
                type="",
                country="",
            ),
            WithholdingRow(
                currency="EUR",
                date=dt.date(2024, 1, 2),
                description="Alpha",
                amount=Decimal("-1.5"),
                code="",
                type="",
                country="",
            ),
        ]
    )

    out_path = tmp_path / "withholding_sorted.xlsx"
    sink = ExcelReportSink(out_path=out_path, locale="EN")
    sink.write(rb)

    wb = load_workbook(out_path)
    ws = wb["Withholding Tax"]
    rows = [
        (ws.cell(row=i, column=2).value, ws.cell(row=i, column=3).value)
        for i in range(2, ws.max_row + 1)
    ]
    assert rows == sorted(rows, key=lambda r: (r[0], r[1]))
