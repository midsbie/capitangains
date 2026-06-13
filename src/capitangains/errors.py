"""Domain error types shared across the extraction/validation pipeline."""

# Process exit codes, named once for the CLI boundary (diagnostics / pipeline).
# EXIT_DATA_QUALITY: a curated fail-closed gate rejected understood-but-invalid input
# (a DataQualityError or an equivalent precondition). EXIT_SETUP: a setup failure such
# as an unusable FX table, a class apart from the data gates.
EXIT_DATA_QUALITY = 2
EXIT_SETUP = 1


class DataQualityError(ValueError):
    """A predictable, user-facing defect in the input data.

    Raised by extraction and validation when a row is malformed or violates an invariant
    (missing required field, unparseable number/date, broken symbol-currency
    uniqueness). The CLI boundary catches it and exits EXIT_DATA_QUALITY ("input
    rejected") with the message, rather than letting it escape as a raw traceback.

    Subclasses ``ValueError`` deliberately: a data-quality problem *is* a value error,
    and existing callers/tests that already handle ``ValueError`` keep working
    unchanged. The boundary catch stays narrow (``except DataQualityError``) so genuine,
    unclassified bugs still surface loudly as tracebacks instead of being masked.
    """
