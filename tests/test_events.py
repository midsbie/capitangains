from capitangains.reporting.events import EventRecorder
from capitangains.reporting.fifo_domain import GapResolution
from tests.support import make_gap_event


def test_event_recorder_collects_and_exposes_list_reference():
    recorder = EventRecorder()
    event = make_gap_event(symbol="ABC", outcome=GapResolution.UNACKNOWLEDGED)
    recorder.record_gap(event)

    assert recorder.gap_events[-1] is event
