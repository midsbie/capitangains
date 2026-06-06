import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook

from capitangains.reporting import detect_symbol_currency_violations
from capitangains.reporting.extract import (
    DividendRow,
    InterestRow,
    SyepInterestRow,
    TradeRow,
    WithholdingRow,
)
from capitangains.reporting.fifo_domain import RealizedLine, SellMatchLeg
from capitangains.reporting.fx import FxTable
from capitangains.reporting.i18n import LABELS, labels_for
from capitangains.reporting.report_builder import ReportBuilder
from capitangains.reporting.report_sink import ExcelReportSink, _gap_status


def _make_fx(rates):
    table = FxTable()
    for (ccy, date), value in rates.items():
        table.data[ccy][dt.date.fromisoformat(date)] = value
    for ccy, m in table.data.items():
        table.date_index[ccy] = sorted(m.keys())
    return table


def _realized(
    symbol: str,
    currency: str,
    sell_date: dt.date,
    legs: list[dict[str, Any]],
    *,
    has_gap: bool = False,
    gap_fixed: bool = False,
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
        has_gap=has_gap,
        gap_fixed=gap_fixed,
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


def test_multi_currency_same_symbol_detected():
    """Same symbol in multiple currencies is a detected violation."""
    trades = [_trade_row("ABC", "USD"), _trade_row("ABC", "EUR")]
    assert detect_symbol_currency_violations(trades, []) == {
        "ABC": frozenset({"USD", "EUR"})
    }


def test_convert_eur_leaves_line_unconverted_when_any_rate_missing():
    """A missing buy- or sell-date rate leaves the entire line unconverted and records
    every unresolved (date, currency); no substitution of another date's rate."""
    rb = ReportBuilder(year=2024)
    # Sell-date rate present, but the first leg's buy date predates the table, missing.
    usd_legs: list[dict[str, Any]] = [
        {
            "buy_date": dt.date(2023, 6, 1),
            "qty": Decimal("5"),
            "alloc_cost_ccy": Decimal("40"),
        },
        {  # zero-cost gap leg: no buy date, cost 0
            "buy_date": None,
            "qty": Decimal("5"),
            "alloc_cost_ccy": Decimal("0"),
        },
    ]
    rl_usd = _realized("USD1", "USD", dt.date(2024, 3, 1), usd_legs)
    # GBP entirely absent from the table, so even the sell-date rate is missing.
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

    rb.convert_eur(_make_fx({("USD", "2024-03-01"): Decimal("0.9")}))

    # USD line left wholly unconverted because one leg's buy-date rate is missing,
    # even though the sell-date rate was available.
    assert all(leg.alloc_cost_eur is None for leg in rl_usd.legs)
    assert rl_usd.alloc_cost_eur is None
    assert rl_usd.realized_pl_eur is None
    assert rl_usd.sell_net_eur is None
    # Every unresolved lookup is recorded for the CLI to abort on.
    assert rb.fx_missing == {
        (dt.date(2023, 6, 1), "USD"),
        (dt.date(2024, 1, 10), "GBP"),
        (dt.date(2024, 2, 1), "GBP"),
    }


def test_convert_eur_zero_sell_qty_skips_proceeds_allocation():
    rb = ReportBuilder(year=2024)
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
    rb.convert_eur(_make_fx({("USD", "2024-04-01"): Decimal("0.9")}))
    assert not rb.fx_missing
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


def test_gap_status_reflects_basis_provenance():
    leg = {
        "buy_date": dt.date(2023, 1, 1),
        "qty": Decimal("5"),
        "alloc_cost_ccy": Decimal("40"),
    }
    clean = _realized("AAA", "USD", dt.date(2024, 1, 1), [leg])
    synth = _realized(
        "BBB", "USD", dt.date(2024, 1, 2), [leg], has_gap=True, gap_fixed=True
    )
    zero = _realized("CCC", "USD", dt.date(2024, 1, 3), [leg], has_gap=True)

    assert _gap_status(clean) == ""
    assert _gap_status(synth) == "synthesized from Basis"
    assert _gap_status(zero) == "zero-cost gap"


def test_realized_sheet_flags_synthesized_basis(tmp_path):
    rb = ReportBuilder(year=2024)
    leg = {
        "buy_date": dt.date(2023, 1, 1),
        "qty": Decimal("5"),
        "alloc_cost_ccy": Decimal("40"),
    }
    rb.add_realized(
        _realized(
            "SYN", "USD", dt.date(2024, 1, 1), [leg], has_gap=True, gap_fixed=True
        )
    )
    rb.convert_eur(_make_fx({("USD", "2024-01-01"): Decimal("0.9")}))

    out_path = tmp_path / "synth.xlsx"
    ExcelReportSink(out_path=out_path, locale="EN").write(rb)

    ws = load_workbook(out_path)["Realized Trades"]
    col = ws.max_column  # "Basis Status" is appended last
    assert ws.cell(row=1, column=col).value == "Basis Status"
    assert ws.cell(row=2, column=col).value == "synthesized from Basis"


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
        (
            "USD",
            {
                ("USD", "2024-06-15"): Decimal("0.9"),  # sell date
                ("USD", "2023-01-01"): Decimal("0.9"),  # buy dates
                ("USD", "2023-01-02"): Decimal("0.9"),
                ("USD", "2023-01-03"): Decimal("0.9"),
            },
        ),
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


def test_every_label_field_defines_both_locales():
    """Locale parity: each field must carry exactly the same set of locales.

    The canonical label table co-locates the translations per field precisely so a key
    can never exist in one language alone (separate per-locale dicts could diverge,
    surfacing only as a write-time KeyError in the affected locale). Deriving the locale
    set from the data keeps this honest if a third locale is ever added.
    """
    field_locale_sets = {
        (section, field): frozenset(translations)
        for section, fields in LABELS.items()
        for field, translations in fields.items()
    }
    assert field_locale_sets, "label table is empty"

    expected = {"EN", "PT"}
    diverging = {
        key: sorted(locs)
        for key, locs in field_locale_sets.items()
        if set(locs) != expected
    }
    assert not diverging, f"fields not covering exactly {sorted(expected)}: {diverging}"

    # No empty translations slip through.
    blank = [
        (section, field, loc)
        for section, fields in LABELS.items()
        for field, translations in fields.items()
        for loc, text in translations.items()
        if not text.strip()
    ]
    assert not blank, f"blank label text: {blank}"


def test_pt_projection_renders_portuguese_labels():
    """PT had no text coverage before the unification; pin a representative sample.

    Includes the Anexo-J realized-P/L header, whose PT text carries a non-breaking
    hyphen (U+2011) that must survive byte-for-byte.
    """
    pt = labels_for("PT")
    assert pt["sheet"]["summary"] == "Totais de Operações"
    assert pt["realized"]["ticker"] == "Símbolo"
    assert pt["anexo_j"]["pl_eur"] == "Mais/menos-valia (EUR)"
    # Any unrecognized locale falls back to PT (the report's default).
    assert labels_for("XX") == pt
