"""Unit tests for parse_open_positions (IBKR 'Open Positions' cost-basis extraction)."""

from decimal import Decimal

from capitangains.conv import Currency
from capitangains.reporting.extract import parse_open_positions
from tests.support import (
    OPEN_POSITIONS_COLUMNS,
    header_row,
    open_position_data,
    parse_model,
)


def _model(*data_rows):
    return parse_model(
        [header_row("Open Positions", OPEN_POSITIONS_COLUMNS), *data_rows]
    )


def test_parse_open_positions_reads_cost_basis_per_symbol():
    # The real AZN/LLOY 2021 snapshots: Cost Basis is what was paid, not the Market
    # Value the Transfers section carries.
    model = _model(
        open_position_data(),  # AZN, qty 10, Cost Basis 708.495
        open_position_data(
            symbol="LLOY",
            quantity="2500",
            cost_price="0.389325",
            cost_basis="973.3125",
            close_price="0.478",
            value="1195",
            unrealized_pl="221.6875",
        ),
    )
    positions, defects = parse_open_positions(model)

    assert defects == []
    by_symbol = {p.symbol: p for p in positions}
    assert by_symbol["AZN"].currency == Currency("GBP")
    assert by_symbol["AZN"].quantity == Decimal("10")
    assert by_symbol["AZN"].cost_basis == Decimal("708.495")
    assert by_symbol["LLOY"].quantity == Decimal("2500")
    assert by_symbol["LLOY"].cost_basis == Decimal("973.3125")


def test_parse_open_positions_skips_non_summary_and_non_stock_rows():
    # Only the per-symbol Summary of a stock-like category is kept: a per-lot 'Lot' row
    # (would double-count the same AZN position) and a non-stock 'Forex' row are
    # dropped, not rejected.
    model = _model(
        open_position_data(),  # kept: Summary, Stocks
        open_position_data(discriminator="Lot"),  # skipped: per-lot breakdown
        open_position_data(
            asset_category="Forex", symbol="EUR.USD", currency="USD"
        ),  # skipped: non-stock
    )
    positions, defects = parse_open_positions(model)

    assert defects == []
    assert [p.symbol for p in positions] == ["AZN"]


def test_parse_open_positions_defect_on_malformed_cost_basis():
    # A Cost Basis that does not parse is a defect (the snapshot exists to supply a
    # basis): the row is collected, not silently dropped, and locates its symbol.
    model = _model(open_position_data(cost_basis="7O8.495"))
    positions, defects = parse_open_positions(model)

    assert positions == []
    assert len(defects) == 1
    assert defects[0].section == "Open Positions"
    assert defects[0].symbol == "AZN"
    assert "Cost Basis" in defects[0].reason
