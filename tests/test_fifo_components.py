import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from capitangains.reporting.fifo_domain import Lot
from capitangains.reporting.positions import PositionBook
from capitangains.reporting.gap_policy import BasisSynthesisPolicy
from capitangains.reporting.realized_builder import build_realized_line


def test_position_book_fifo_consumption_and_residual_tracking():
    book = PositionBook()
    book.append_buy(
        "ABC",
        Lot(dt.date(2024, 1, 1), Decimal("100"), Decimal("1000"), "USD"),
    )
    book.append_buy(
        "ABC",
        Lot(dt.date(2024, 2, 1), Decimal("50"), Decimal("600"), "USD"),
    )

    legs, alloc, remaining = book.consume_fifo("ABC", Decimal("120"))
    assert remaining == Decimal("0")
    assert len(legs) == 2
    assert legs[0]["qty"] == Decimal("100")
    assert legs[0]["alloc_cost_ccy"] == Decimal("1000.00000000")
    assert legs[1]["qty"] == Decimal("20")
    assert legs[1]["alloc_cost_ccy"] == Decimal("240.00000000")
    assert alloc == Decimal("1240.00000000")

    legs2, alloc2, remaining2 = book.consume_fifo("ABC", Decimal("50"))
    # 30 available from previous lot, 20 shortage
    assert len(legs2) == 1
    assert legs2[0]["qty"] == Decimal("30")
    assert legs2[0]["alloc_cost_ccy"] == Decimal("360.00000000")
    assert alloc2 == Decimal("360.00000000")
    assert remaining2 == Decimal("20")


def test_position_book_validations():
    book = PositionBook()
    with pytest.raises(ValueError):
        book.append_buy(
            "XYZ",
            Lot(dt.date(2024, 1, 1), Decimal("0"), Decimal("0"), "USD"),
        )
    with pytest.raises(ValueError):
        book.append_buy(
            "XYZ",
            Lot(dt.date(2024, 1, 1), Decimal("-5"), Decimal("0"), "USD"),
        )
    lot = Lot(dt.date(2024, 1, 2), Decimal("10"), Decimal("100"), "USD")
    book.append_buy("XYZ", lot)
    with pytest.raises(ValueError):
        book.consume_fifo("XYZ", Decimal("0"))


def test_position_book_returns_remainder_when_no_lots():
    book = PositionBook()
    legs, alloc, remaining = book.consume_fifo("MISSING", Decimal("5"))
    assert legs == []
    assert alloc == Decimal("0")
    assert remaining == Decimal("5")


def test_basis_synthesis_policy_within_tolerance_clamps_to_zero():
    trade = SimpleNamespace(
        symbol="ABC",
        date=dt.date(2024, 3, 1),
        currency="USD",
        basis_ccy=Decimal("-1200"),
    )
    policy = BasisSynthesisPolicy(tolerance=Decimal("0.02"), basis_getter=lambda t: t.basis_ccy)
    legs = [
        {
            "buy_date": dt.date(2024, 1, 1),
            "qty": Decimal("100"),
            "lot_qty_before": Decimal("100"),
            "alloc_cost_ccy": Decimal("1200.01000000"),
        }
    ]
    legs_after, alloc_after, event = policy.resolve(
        trade, Decimal("20"), legs, Decimal("1200.01000000")
    )
    assert event.fixed is True
    assert legs_after[-1]["synthetic"] is True
    assert legs_after[-1]["alloc_cost_ccy"] == Decimal("0.00000000")
    assert alloc_after == Decimal("1200.01000000")


def test_basis_synthesis_policy_guardrails_fallback_to_zero_cost():
    trade = SimpleNamespace(
        symbol="DEF",
        date=dt.date(2024, 3, 2),
        currency="USD",
        basis_ccy=Decimal("-900"),
    )
    policy = BasisSynthesisPolicy(tolerance=Decimal("0.02"), basis_getter=lambda t: t.basis_ccy)
    legs: list[dict] = []
    legs_after, alloc_after, event = policy.resolve(
        trade, Decimal("15"), legs, Decimal("950")
    )
    assert event.fixed is False
    assert legs_after[-1]["alloc_cost_ccy"] == Decimal("0.00000000")
    assert alloc_after == Decimal("950")


def test_basis_synthesis_policy_missing_basis_uses_strict_gap():
    trade = SimpleNamespace(
        symbol="GHI",
        date=dt.date(2024, 3, 3),
        currency="USD",
        basis_ccy=None,
    )
    policy = BasisSynthesisPolicy(tolerance=Decimal("0.02"), basis_getter=lambda t: t.basis_ccy)
    legs: list[dict] = []
    legs_after, alloc_after, event = policy.resolve(
        trade, Decimal("5"), legs, Decimal("0")
    )
    assert event.fixed is False
    assert legs_after[-1]["alloc_cost_ccy"] == Decimal("0.00000000")
    assert alloc_after == Decimal("0")


def test_basis_synthesis_policy_residual_equal_tolerance_clamps():
    trade = SimpleNamespace(
        symbol="HIJ",
        date=dt.date(2024, 3, 4),
        currency="USD",
        basis_ccy=Decimal("-1000"),
    )
    policy = BasisSynthesisPolicy(tolerance=Decimal("0.02"), basis_getter=lambda t: t.basis_ccy)
    legs = [
        {
            "buy_date": dt.date(2024, 1, 1),
            "qty": Decimal("100"),
            "lot_qty_before": Decimal("100"),
            "alloc_cost_ccy": Decimal("1000.02000000"),
        }
    ]
    legs_after, alloc_after, event = policy.resolve(
        trade, Decimal("10"), legs, Decimal("1000.02000000")
    )
    assert event.fixed is True
    assert legs_after[-1]["alloc_cost_ccy"] == Decimal("0.00000000")
    assert alloc_after == Decimal("1000.02000000")


def test_realized_line_builder_rounds_realized_pl():
    trade = SimpleNamespace(
        symbol="JKL",
        date=dt.date(2024, 4, 1),
        currency="USD",
        quantity=Decimal("-50"),
        proceeds=Decimal("500.1234"),
        comm_fee=Decimal("-1.23"),
    )
    legs = [
        {
            "buy_date": dt.date(2023, 6, 1),
            "qty": Decimal("50"),
            "lot_qty_before": Decimal("50"),
            "alloc_cost_ccy": Decimal("420.56789012"),
        }
    ]
    line = build_realized_line(trade, legs, Decimal("420.56789012"))
    assert line.sell_qty == Decimal("50")
    assert line.sell_gross_ccy == Decimal("500.1234")
    assert line.sell_net_ccy == Decimal("498.8934")
    assert line.realized_pl_ccy == Decimal("78.33")
    assert line.legs is not legs
