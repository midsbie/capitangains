from capitangains.reporting.events import EventRecorder
from capitangains.reporting.fifo_domain import GapResolution
from tests.support import make_gap_event


def test_event_recorder_collects_and_exposes_list_reference():
    recorder = EventRecorder()
    event = make_gap_event(symbol="ABC", outcome=GapResolution.UNACKNOWLEDGED)
    recorder.record_gap(event)

    assert recorder.gap_events[-1] is event


def test_event_recorder_record_many_and_clear():
    recorder = EventRecorder()
    events = [
        make_gap_event(symbol="XYZ", outcome=GapResolution.SYNTHESIZED),
        make_gap_event(symbol="LMN", outcome=GapResolution.UNACKNOWLEDGED),
    ]
    recorder.record_many(events)

    assert recorder.gap_events == events

    recorder.clear()
    assert recorder.gap_events == []
