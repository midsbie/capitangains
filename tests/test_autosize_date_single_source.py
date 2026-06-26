"""A date column's autosized width must be single-sourced from NumberFormats.date.

Column widths are accumulated as cells are written (no post-hoc rescan), and a date cell
is sized through NumberFormats.date, the one place date presentation lives. Both
supported locales render a date in exactly 10 characters (PT "01/02/2024", EN
"2024-02-01"), so a regression that re-derived the width from its own strftime would
look correct in normal operation. This test drives a wedge they cannot show on their own
by widening the canonical date format, then asserts the column follows it: a second,
independent date-width derivation would keep the old 10-char width and fail here.

Only the single-source property is asserted. That width measurement folds into the write
pass (rather than a second O(rows x cols) scan) makes the build faster, not different; a
cell-read count test would pin an implementation detail the project forbids.
"""

import datetime as dt

from openpyxl import Workbook

from capitangains.reporting.i18n import NumberFormats
from capitangains.reporting.report_builder import ReportBuilder
from capitangains.reporting.report_sink import _DateColumn, _SheetSpec, _SheetWriter


def test_date_column_width_follows_number_formats(monkeypatch):
    # A 19-char format, comfortably inside the autosizer's [min=10, max=60] band, so a
    # single-sourced width is exactly the format length plus the 2-char padding.
    monkeypatch.setattr(
        NumberFormats, "date", property(lambda self: "DD/MM/YYYY HH:MM:SS")
    )
    formats = NumberFormats("PT")
    labels = {"sheet": {"dates": "Dates"}, "cols": {"d": "Data"}}
    spec: _SheetSpec[dt.date] = _SheetSpec(
        sheet_key="dates",
        label_section="cols",
        columns=(_DateColumn[dt.date]("d", value=lambda r: r),),
        rows=lambda _report: [dt.date(2024, 2, 1), dt.date(2024, 3, 15)],
    )

    writer = _SheetWriter(Workbook(), labels, formats)
    writer.write_table(spec, ReportBuilder(year=2024))

    width = writer.wb["Dates"].column_dimensions["A"].width
    assert width == len(formats.date) + 2
