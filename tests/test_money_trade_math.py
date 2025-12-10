from decimal import Decimal

from capitangains.reporting.money import (
    abs_decimal,
    quantize_allocation,
    quantize_money,
    round_cost_piece,
)
from capitangains.reporting.trade_math import buy_cost_ccy, sell_gross_ccy, sell_net_ccy


def test_quantize_money_custom_places():
    value = Decimal("123.4567")
    assert quantize_money(value) == Decimal("123.46")
    assert quantize_money(value, "0.0001") == Decimal("123.4567")


def test_quantize_allocation_rounding():
    value = Decimal("0.123456789")
    assert quantize_allocation(value) == Decimal("0.12345679")


def test_round_cost_piece_proportional_allocation():
    total_basis = Decimal("100.00")
    take = Decimal("25")
    lot_qty = Decimal("100")
    assert round_cost_piece(total_basis, take, lot_qty) == Decimal("25.00000000")


def test_round_cost_piece_handles_zero_qty_lot():
    assert round_cost_piece(Decimal("100"), Decimal("10"), Decimal("0")) == Decimal("0")


def test_round_cost_piece_does_not_exceed_basis():
    total_basis = Decimal("100.00")
    take = Decimal("33.34")
    lot_qty = Decimal("100")
    piece = round_cost_piece(total_basis, take, lot_qty)
    assert piece <= total_basis


def test_abs_decimal_uses_copy_abs():
    value = Decimal("-10.5")
    assert abs_decimal(value) == Decimal("10.5")
    assert value == Decimal("-10.5")


def test_buy_cost_ccy_matches_sign_convention():
    proceeds = Decimal("-1000")
    comm = Decimal("-5")
    assert buy_cost_ccy(proceeds, comm) == Decimal("1005")


def test_sell_gross_and_net_ccy():
    proceeds = Decimal("500")
    comm = Decimal("-2")
    assert sell_gross_ccy(proceeds) == Decimal("500")
    assert sell_net_ccy(proceeds, comm) == Decimal("498")


def test_sell_net_handles_commission_rebate():
    proceeds = Decimal("500")
    comm = Decimal("1")  # rebate
    assert sell_net_ccy(proceeds, comm) == Decimal("501")
