import datetime as dt
from decimal import Decimal

import pytest
from fixtures import Trade

from capitangains.reporting.fifo_domain import GapResolution, Lot, SellMatchLeg
from capitangains.reporting.gap_policy import BasisSynthesisPolicy
from capitangains.reporting.positions import PositionBook
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

    legs, alloc, remaining = book.consume_fifo("ABC", "USD", Decimal("120"))
    assert remaining == Decimal("0")
    assert len(legs) == 2
    assert legs[0].qty == Decimal("100")
    assert legs[0].alloc_cost_ccy == Decimal("1000.00000000")
    assert legs[1].qty == Decimal("20")
    assert legs[1].alloc_cost_ccy == Decimal("240.00000000")
    assert alloc == Decimal("1240.00000000")

    legs2, alloc2, remaining2 = book.consume_fifo("ABC", "USD", Decimal("50"))
    # 30 available from previous lot, 20 shortage
    assert len(legs2) == 1
    assert legs2[0].qty == Decimal("30")
    assert legs2[0].alloc_cost_ccy == Decimal("360.00000000")
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
        book.consume_fifo("XYZ", "USD", Decimal("0"))


def test_position_book_returns_remainder_when_no_lots():
    book = PositionBook()
    legs, alloc, remaining = book.consume_fifo("MISSING", "USD", Decimal("5"))
    assert legs == []
    assert alloc == Decimal("0")
    assert remaining == Decimal("5")


def test_position_book_consume_fifo_isolates_currencies():
    book = PositionBook()
    book.append_buy(
        "XYZ",
        Lot(dt.date(2024, 1, 1), Decimal("100"), Decimal("1000"), "EUR"),
    )
    book.append_buy(
        "XYZ",
        Lot(dt.date(2024, 2, 1), Decimal("50"), Decimal("600"), "USD"),
    )

    legs, alloc, remaining = book.consume_fifo("XYZ", "USD", Decimal("50"))
    assert remaining == Decimal("0")
    assert len(legs) == 1
    assert alloc == Decimal("600.00000000")

    # EUR lot untouched
    legs_eur, alloc_eur, remaining_eur = book.consume_fifo("XYZ", "EUR", Decimal("100"))
    assert remaining_eur == Decimal("0")
    assert len(legs_eur) == 1
    assert alloc_eur == Decimal("1000.00000000")


def test_basis_synthesis_policy_within_tolerance_clamps_to_zero():
    trade = Trade(
        symbol="ABC",
        date=dt.date(2024, 3, 1),
        currency="USD",
        quantity=Decimal("-100"),
        proceeds=Decimal("1200"),
        comm_fee=Decimal("0"),
        basis_ccy=Decimal("-1200"),
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: None,
    )
    result = policy.resolve(trade, Decimal("20"), Decimal("1200.01000000"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.SYNTHESIZED
    assert result.leg.synthetic is True
    assert result.leg.alloc_cost_ccy == Decimal("0.00000000")
    assert result.alloc_cost == Decimal("1200.01000000")


def test_basis_synthesis_policy_negative_residual_beyond_tolerance_is_defective():
    trade = Trade(
        symbol="DEF",
        date=dt.date(2024, 3, 2),
        currency="USD",
        quantity=Decimal("-15"),
        proceeds=Decimal("900"),
        comm_fee=Decimal("0"),
        basis_ccy=Decimal("-900"),
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: None,
    )
    result = policy.resolve(trade, Decimal("15"), Decimal("950"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.DEFECTIVE
    assert result.leg.alloc_cost_ccy == Decimal("0.00000000")
    assert result.alloc_cost == Decimal("950")


def test_basis_synthesis_policy_missing_basis_is_defective():
    trade = Trade(
        symbol="GHI",
        date=dt.date(2024, 3, 3),
        currency="USD",
        quantity=Decimal("-5"),
        proceeds=Decimal("100"),
        comm_fee=Decimal("0"),
        basis_ccy=None,
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: None,
    )
    result = policy.resolve(trade, Decimal("5"), Decimal("0"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.DEFECTIVE
    assert result.leg.alloc_cost_ccy == Decimal("0.00000000")
    assert result.alloc_cost == Decimal("0")


def test_basis_synthesis_policy_residual_equal_tolerance_clamps():
    trade = Trade(
        symbol="HIJ",
        date=dt.date(2024, 3, 4),
        currency="USD",
        quantity=Decimal("-10"),
        proceeds=Decimal("1000"),
        comm_fee=Decimal("0"),
        basis_ccy=Decimal("-1000"),
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: None,
    )
    result = policy.resolve(trade, Decimal("10"), Decimal("1000.02000000"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.SYNTHESIZED
    assert result.leg.alloc_cost_ccy == Decimal("0.00000000")
    assert result.alloc_cost == Decimal("1000.02000000")


def test_basis_synthesis_policy_rejects_basis_inconsistent_with_realized():
    # IBKR's columns satisfy Proceeds + Comm + Basis = Realized; a Basis that breaks the
    # identity is corrupt, so synthesis flags it DEFECTIVE (the boundary then aborts)
    # rather than fabricating a cost from it.
    trade = Trade(
        symbol="BAD",
        date=dt.date(2024, 3, 5),
        currency="USD",
        quantity=Decimal("-10"),
        proceeds=Decimal("1200"),
        comm_fee=Decimal("0"),
        basis_ccy=Decimal("-9999"),  # corrupt: the true basis would be ~-1000
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: Decimal("200"),  # 1200 + 0 - 1000 (the true basis)
    )
    result = policy.resolve(trade, Decimal("10"), Decimal("0"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.DEFECTIVE
    assert "Basis" in result.event.message and "Realized" in result.event.message


def test_basis_synthesis_policy_accepts_near_total_loss_satisfying_identity():
    # basis >> proceeds (a penny-stock collapse) is legitimate when the IBKR identity
    # holds. A magnitude factor would wrongly reject it; the identity check accepts it.
    trade = Trade(
        symbol="PNY",
        date=dt.date(2024, 3, 6),
        currency="USD",
        quantity=Decimal("-50000"),
        proceeds=Decimal("450"),
        comm_fee=Decimal("-12.84851"),
        basis_ccy=Decimal("-550.45"),
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: Decimal("-113.29851"),  # 450 - 12.84851 - 550.45
    )
    result = policy.resolve(trade, Decimal("50000"), Decimal("0"))
    assert result.event is not None
    assert result.event.outcome is GapResolution.SYNTHESIZED
    assert result.leg.synthetic is True
    # synthesized to the (valid) Basis
    assert result.alloc_cost == Decimal("550.45000000")


def test_basis_synthesis_policy_skips_identity_check_without_realized():
    # No IBKR Realized P/L -> nothing to check the Basis against -> synthesize.
    trade = Trade(
        symbol="NOR",
        date=dt.date(2024, 3, 7),
        currency="USD",
        quantity=Decimal("-10"),
        proceeds=Decimal("1200"),
        comm_fee=Decimal("0"),
        basis_ccy=Decimal("-9999"),  # inconsistent, but no Realized to catch it
    )
    policy = BasisSynthesisPolicy(
        tolerance=Decimal("0.02"),
        basis_getter=lambda t: getattr(t, "basis_ccy", None),
        realized_getter=lambda t: None,
    )
    result = policy.resolve(trade, Decimal("10"), Decimal("0"))
    # No Realized P/L -> the identity check cannot fire -> synthesize.
    assert result.event is not None
    assert result.event.outcome is GapResolution.SYNTHESIZED
    assert result.alloc_cost == Decimal("9999.00000000")


def test_realized_line_builder_rounds_realized_pl():
    trade = Trade(
        symbol="JKL",
        date=dt.date(2024, 4, 1),
        currency="USD",
        quantity=Decimal("-50"),
        proceeds=Decimal("500.1234"),
        comm_fee=Decimal("-1.23"),
    )
    legs = [
        SellMatchLeg(
            buy_date=dt.date(2023, 6, 1),
            qty=Decimal("50"),
            lot_qty_before=Decimal("50"),
            alloc_cost_ccy=Decimal("420.56789012"),
        )
    ]
    line = build_realized_line(trade, legs, Decimal("420.56789012"))
    assert line.sell_qty == Decimal("50")
    assert line.sell_gross_ccy == Decimal("500.1234")
    assert line.sell_net_ccy == Decimal("498.8934")
    assert line.realized_pl_ccy == Decimal("78.33")
    assert line.legs is not legs
