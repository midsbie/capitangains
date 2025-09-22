import datetime as dt
from decimal import Decimal

from capitangains.reporting.events import EventRecorder
from capitangains.reporting.fifo_domain import GapEvent


def _make_event(symbol: str, fixed: bool) -> GapEvent:
    return GapEvent(
        symbol=symbol,
        date=dt.date(2024, 1, 1),
        remaining_qty=Decimal("1"),
        currency="USD",
        message="test",
        fixed=fixed,
    )


def test_event_recorder_collects_and_exposes_list_reference():
    recorder = EventRecorder()
    event = _make_event("ABC", fixed=False)
    recorder.record_gap(event)

    assert recorder.gap_events[-1] is event


def test_event_recorder_record_many_and_clear():
    recorder = EventRecorder()
    events = [_make_event("XYZ", fixed=True), _make_event("LMN", fixed=False)]
    recorder.record_many(events)

    assert recorder.gap_events == events

    recorder.clear()
    assert recorder.gap_events == []
