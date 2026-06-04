"""Domain error types shared across the extraction/validation pipeline."""


class DataQualityError(ValueError):
    """A predictable, user-facing defect in the input data.

    Raised by extraction and validation when a row is malformed or violates an
    invariant (missing required field, unparseable number/date, broken symbol-currency
    uniqueness). The CLI boundary catches it and exits 2 ("input rejected") with the
    message, rather than letting it escape as a raw traceback (exit 1).

    Subclasses ``ValueError`` deliberately: a data-quality problem *is* a value error,
    and existing callers/tests that already handle ``ValueError`` keep working
    unchanged. The boundary catch stays narrow (``except DataQualityError``) so genuine,
    unclassified bugs still surface loudly as tracebacks instead of being masked.
    """
