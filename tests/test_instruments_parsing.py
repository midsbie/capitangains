"""Unit tests for parse_instruments (IBKR Financial Instrument Information -> ISIN)."""

from capitangains.reporting.extract import instrument_country_map, parse_instruments
from capitangains.reporting.extract.sections import SEC_INSTRUMENTS
from tests.support import parse_model

_COLUMNS = [
    "Asset Category",
    "Symbol",
    "Description",
    "Conid",
    "Security ID",
    "Underlying",
    "Listing Exch",
    "Multiplier",
    "Type",
    "Code",
]


def _row(
    asset: str = "Stocks", symbol: str = "AAL", isin: str = "US02376R1023"
) -> list:
    cells = {
        "Asset Category": asset,
        "Symbol": symbol,
        "Description": "DESC",
        "Conid": "1",
        "Security ID": isin,
        "Underlying": symbol,
        "Listing Exch": "NASDAQ",
        "Multiplier": "1",
        "Type": "COMMON",
        "Code": "",
    }
    return [SEC_INSTRUMENTS, "Data", *(cells[c] for c in _COLUMNS)]


def _model(*data_rows):
    return parse_model([[SEC_INSTRUMENTS, "Header", *_COLUMNS], *data_rows])


def test_parse_instruments_maps_symbol_to_isin_country():
    model = _model(
        _row(symbol="AAL", isin="US02376R1023"),
        _row(symbol="ASML", isin="NL0010273215"),
        _row(symbol="AZN", isin="GB0009895292"),
    )
    rows = parse_instruments(model)
    assert {r.symbol: r.isin for r in rows} == {
        "AAL": "US02376R1023",
        "ASML": "NL0010273215",
        "AZN": "GB0009895292",
    }
    # The country proxy is the ISIN's 2-letter prefix.
    assert instrument_country_map(rows) == {"AAL": "US", "ASML": "NL", "AZN": "GB"}


def test_parse_instruments_skips_non_stock_and_incomplete_rows():
    # Enrichment is lenient: a non-stock row, a blank Security ID, an absent symbol, and
    # an implausibly short ISIN are all dropped rather than rejected.
    model = _model(
        _row(symbol="AAL", isin="US02376R1023"),  # kept
        _row(asset="Forex", symbol="EUR.USD", isin=""),  # non-stock
        _row(symbol="NOISIN", isin=""),  # blank ISIN
        _row(symbol="", isin="US0079031078"),  # no symbol
        _row(symbol="SHORT", isin="US123"),  # too short to carry a country prefix
    )
    assert [r.symbol for r in parse_instruments(model)] == ["AAL"]
