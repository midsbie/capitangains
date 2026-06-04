from capitangains.errors import DataQualityError


def test_data_quality_error_is_value_error():
    # Subclassing ValueError preserves the strict-converter contract and the existing
    # `except ValueError` / pytest.raises(ValueError) callers across the extractors,
    # while the CLI boundary still catches it narrowly to exit 2.
    assert issubclass(DataQualityError, ValueError)
