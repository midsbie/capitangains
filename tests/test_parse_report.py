"""ParseReport diagnostics API: the info/warn/error severity tiers and log_with.

Infrastructure for surfacing parser decisions. The benign ``info`` tier is hidden by
default (root level WARNING) and appears at ``-v`` (INFO); it must not trip
``has_errors``.
"""

import logging

from capitangains.model.ibkr import ParseReport


def test_info_records_info_severity_and_is_not_an_error():
    report = ParseReport()
    report.info(7, "skipped empty-kind summary line")

    assert [i.severity for i in report.issues] == ["info"]
    assert report.has_errors is False


def test_log_with_maps_each_severity_to_its_level(caplog):
    report = ParseReport()
    report.info(1, "benign decision")
    report.warn(2, "output may be wrong")
    report.error(3, "fatal")

    logger = logging.getLogger("test_log_with")
    with caplog.at_level(logging.INFO):
        report.log_with(logger)

    assert [r.levelno for r in caplog.records] == [
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
    ]
    assert [r.getMessage() for r in caplog.records] == [
        "line 1: benign decision",
        "line 2: output may be wrong",
        "line 3: fatal",
    ]


def test_log_with_includes_row_preview_when_present(caplog):
    report = ParseReport()
    report.info(4, "padded short row", ["A", "B"])

    logger = logging.getLogger("test_log_with_row")
    with caplog.at_level(logging.INFO):
        report.log_with(logger)

    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert record.getMessage() == "line 4: padded short row | row=['A', 'B']"
