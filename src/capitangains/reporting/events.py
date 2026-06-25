from __future__ import annotations

from .fifo_domain import GapEvent, TransferShortfall


class EventRecorder:
    """Collect FIFO-core events (SELL gaps, transfer shortfalls), no side effects."""

    def __init__(self) -> None:
        self._gap_events: list[GapEvent] = []
        self._transfer_shortfalls: list[TransferShortfall] = []

    def record_gap(self, event: GapEvent) -> None:
        self._gap_events.append(event)

    def record_transfer_shortfall(self, shortfall: TransferShortfall) -> None:
        self._transfer_shortfalls.append(shortfall)

    @property
    def gap_events(self) -> list[GapEvent]:
        return self._gap_events

    @property
    def transfer_shortfalls(self) -> list[TransferShortfall]:
        return self._transfer_shortfalls
