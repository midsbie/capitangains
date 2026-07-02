"""Pure-domain tests for the Anexo J Tabela X country-code mapping."""

from capitangains.reporting.at_country import at_country_code
from capitangains.reporting.validation import detect_unmapped_countries


def test_maps_known_iso_alpha2_to_its_tabela_x_code():
    # The transcribed Tabela X codes are the ISO 3166-1 numeric codes, printed as the
    # table shows them (no leading zeros). They are stable, externally defined values;
    # asserting a few representative ones guards the transcription.
    assert at_country_code("PT") == "620"
    assert at_country_code("DE") == "276"
    assert at_country_code("US") == "840"


def test_lookup_is_case_insensitive():
    # Upper-cases the input, so a caller may pass a lower-case code (ISINs are upper).
    assert at_country_code("us") == "840"
    assert at_country_code("Us") == "840"


def test_returns_none_for_a_country_absent_from_tabela_x():
    # Tabela X omits the uninhabited ISO territories, so a valid ISO code like AQ
    # (Antarctica) is unmapped; None tells the sink to render the raw ISO, never a
    # fabricated number. ZZ is not an ISO country at all.
    assert at_country_code("AQ") is None
    assert at_country_code("ZZ") is None


def test_detect_unmapped_countries_lists_sorted_unique_unmapped():
    # US and DE are in Tabela X; AQ (Antarctica) and BV (Bouvet) are not. Duplicates
    # collapse; the result is sorted.
    assert detect_unmapped_countries(["US", "AQ", "DE", "AQ", "BV"]) == ["AQ", "BV"]


def test_detect_unmapped_countries_empty_when_all_mapped():
    assert detect_unmapped_countries(["US", "PT", "GB", "NL"]) == []
