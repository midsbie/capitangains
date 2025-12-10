from __future__ import annotations

from collections.abc import Iterable
from typing import List

from .fifo_domain import GapEvent


class EventRecorder:
    """Collect gap events without side effects."""

    def __init__(self) -> None:
        self._gap_events: List[GapEvent] = []

    def record_gap(self, event: GapEvent) -> None:
        self._gap_events.append(event)

    def record_many(self, events: Iterable[GapEvent]) -> None:
        for event in events:
            self.record_gap(event)

    @property
    def gap_events(self) -> list[GapEvent]:
        return self._gap_events

    def clear(self) -> None:
        self._gap_events.clear()
