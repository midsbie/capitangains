"""Unit tests for the Quadro 8A income fold (pure, no I/O).

Rows are built directly with the production dataclasses and ``amount_eur`` set
explicitly, mirroring how the builder hands already-converted rows to the aggregator.
"""

import datetime as dt
import inspect
from decimal import Decimal

import pytest

from capitangains.reporting.extract import DividendRow, InterestRow, WithholdingRow
from capitangains.reporting.quadro_8a import (
    IncomeKind,
    Quadro8ALine,
    aggregate_quadro_8a,
    isin_country,
)

# A fixed date for every row: the fold groups by income kind and country, never by date.
_DATE = dt.date(2024, 1, 1)


def _div(description: str, amount_eur: Decimal | None) -> DividendRow:
    return DividendRow(
        currency="USD",
        date=_DATE,
        description=description,
        amount=Decimal("0"),
        amount_eur=amount_eur,
    )


def _interest(description: str, amount_eur: Decimal | None) -> InterestRow:
    return InterestRow(
        currency="USD",
        date=_DATE,
        description=description,
        amount=Decimal("0"),
        amount_eur=amount_eur,
    )


def _wh(
    description: str, wtype: str, country: str, amount_eur: Decimal | None
) -> WithholdingRow:
    return WithholdingRow(
        currency="USD",
        date=_DATE,
        description=description,
        amount=Decimal("0"),
        code="",
        type=wtype,
        country=country,
        amount_eur=amount_eur,
    )


def _aggregate(*, dividends=(), interest=(), withholding=(), broker="IE"):
    return aggregate_quadro_8a(
        dividends=list(dividends),
        interest=list(interest),
        withholding=list(withholding),
        broker_country=broker,
    )


@pytest.mark.parametrize(
    "description, expected",
    [
        ("SLIGR(NL0000817179) Cash Dividend", "NL"),
        ("PACW(US6952631033) Payment in Lieu of Dividend", "US"),
        ("EUR Credit Interest for Sep-2022", None),
        ("garbled(NL00) Cash Dividend", None),
        ("", None),
    ],
)
def test_isin_country(description, expected):
    assert isin_country(description) == expected


def test_pil_is_its_own_line_under_the_dividend_code():
    lines = _aggregate(
        dividends=[
            _div("PACW(US6952631033) Cash Dividend", Decimal("10.00")),
            _div("PACW(US6952631033) Payment in Lieu of Dividend", Decimal("4.00")),
        ]
    )
    by_kind = {line.kind: line for line in lines}
    assert set(by_kind) == {IncomeKind.DIVIDEND, IncomeKind.PIL}
    assert by_kind[IncomeKind.DIVIDEND].income_code == "E11"
    assert by_kind[IncomeKind.PIL].income_code == "E11"
    assert by_kind[IncomeKind.DIVIDEND].gross_eur == Decimal("10.00")
    assert by_kind[IncomeKind.PIL].gross_eur == Decimal("4.00")
    assert by_kind[IncomeKind.DIVIDEND].country == "US"
    assert by_kind[IncomeKind.PIL].country == "US"


def test_syep_interest_is_not_a_parameter_so_cannot_double_count():
    # Structural guarantee: only ``interest`` (which already carries the monthly SYEP
    # summaries) feeds the fold; the per-loan ``syep_interest`` detail has no parameter
    # to flow through, so it cannot be added a second time.
    assert "syep_interest" not in inspect.signature(aggregate_quadro_8a).parameters

    lines = _aggregate(
        interest=[_interest("USD SYEP Interest for Sep-2022", Decimal("3.00"))]
    )
    assert len(lines) == 1
    assert lines[0].kind == IncomeKind.INTEREST
    assert lines[0].gross_eur == Decimal("3.00")


def test_debit_interest_is_excluded_from_the_interest_gross():
    # Debit (margin) interest is a financing cost the account pays, not Box 8A income,
    # so it must not net against credit interest in the E21 gross.
    lines = _aggregate(
        interest=[
            _interest("USD Credit Interest for Sep-2022", Decimal("5.00")),
            _interest("USD Debit Interest for Sep-2022", Decimal("-2.00")),
        ]
    )
    assert len(lines) == 1
    assert lines[0].kind == IncomeKind.INTEREST
    assert lines[0].gross_eur == Decimal("5.00")  # debit interest did not net in


def test_debit_interest_only_produces_no_interest_line():
    lines = _aggregate(
        interest=[_interest("USD Debit Interest for Sep-2022", Decimal("-2.00"))]
    )
    assert lines == []


@pytest.mark.parametrize("broker", ["IE", "LU"])
def test_interest_source_country_is_the_injected_jurisdiction(broker):
    lines = _aggregate(
        interest=[_interest("EUR Credit Interest for Sep-2022", Decimal("2.00"))],
        broker=broker,
    )
    assert len(lines) == 1
    assert lines[0].income_code == "E21"
    assert lines[0].country == broker


def test_group_with_gross_and_no_withholding_has_zero_tax():
    lines = _aggregate(
        dividends=[_div("SLIGR(NL0000817179) Cash Dividend", Decimal("9.00"))]
    )
    assert len(lines) == 1
    assert lines[0].gross_eur == Decimal("9.00")
    assert lines[0].tax_eur == Decimal("0.00")


def test_dividend_and_its_withholding_merge_by_kind_and_country():
    lines = _aggregate(
        dividends=[_div("PACW(US6952631033) Cash Dividend", Decimal("10.00"))],
        withholding=[
            _wh("PACW(US6952631033) Cash Dividend", "Dividend", "US", Decimal("-1.50"))
        ],
    )
    assert lines == [
        Quadro8ALine(
            kind=IncomeKind.DIVIDEND,
            country="US",
            gross_eur=Decimal("10.00"),
            tax_eur=Decimal("1.50"),
        )
    ]


def test_interest_withholding_falls_onto_the_broker_interest_group():
    # Interest withholding carries no country suffix, so it must merge with the gross
    # interest under the broker jurisdiction rather than form a separate line.
    lines = _aggregate(
        interest=[_interest("USD Credit Interest for Sep-2022", Decimal("2.00"))],
        withholding=[
            _wh("USD Credit Interest - Tax", "Interest", "", Decimal("-0.40"))
        ],
        broker="IE",
    )
    assert len(lines) == 1
    assert lines[0].country == "IE"
    assert lines[0].gross_eur == Decimal("2.00")
    assert lines[0].tax_eur == Decimal("0.40")


def test_unpriced_gross_row_is_skipped():
    # A None amount_eur (unpriced row) contributes nothing. The live pipeline aborts
    # on a missing FX rate before reaching here; the pure fold stays total regardless.
    lines = _aggregate(
        dividends=[
            _div("SLIGR(NL0000817179) Cash Dividend", Decimal("9.00")),
            _div("SLIGR(NL0000817179) Cash Dividend", None),
        ]
    )
    assert len(lines) == 1
    assert lines[0].gross_eur == Decimal("9.00")  # the None row did not contribute


def test_unpriced_withholding_row_is_skipped():
    lines = _aggregate(
        dividends=[_div("PACW(US6952631033) Cash Dividend", Decimal("10.00"))],
        withholding=[
            _wh("PACW(US6952631033) Cash Dividend", "Dividend", "US", None),
        ],
    )
    assert len(lines) == 1
    assert lines[0].tax_eur == Decimal("0.00")  # the None row did not contribute


def test_withholding_reversal_nets_against_tax():
    # Withholding accumulates signed, so a positive reversal/refund nets against the
    # negative tax (-1.80 + 0.30) rather than inflating it to 2.10.
    lines = _aggregate(
        dividends=[_div("PACW(US6952631033) Cash Dividend", Decimal("10.00"))],
        withholding=[
            _wh("PACW(US6952631033) Cash Dividend", "Dividend", "US", Decimal("-1.80")),
            _wh("PACW(US6952631033) Cash Dividend", "Dividend", "US", Decimal("0.30")),
        ],
    )
    assert len(lines) == 1
    assert lines[0].tax_eur == Decimal("1.50")


def test_unknown_withholding_is_skipped():
    lines = _aggregate(
        withholding=[_wh("Some mystery line", "Unknown", "GB", Decimal("-3.00"))]
    )
    assert lines == []


def test_withholding_sign_is_reported_as_positive_magnitude():
    lines = _aggregate(
        dividends=[_div("PACW(US6952631033) Cash Dividend", Decimal("10.00"))],
        withholding=[
            _wh("PACW(US6952631033) Cash Dividend", "Dividend", "US", Decimal("-1.80"))
        ],
    )
    assert lines[0].tax_eur == Decimal("1.80")
