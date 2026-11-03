"""The frame sources of an offline entry and how their frames reach the GUI thread."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ax_devil.core.data_types import FrameData
from ax_devil.modules.data_sources.file_frame_source import FileFrameSource


class FrameDeliveryRelay(QObject):
    """Hand frames to the GUI thread, presenting only the newest one when deliveries queue up.

    Deliveries arrive as queued GUI-thread events. The first one posts a flush behind any deliveries already
    queued, so a GUI thread that falls behind skips superseded frames before overlay lookup and presentation.
    """

    frameReady = Signal(int, FrameData)
    _flushRequested = Signal()

    def __init__(self, source_index: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source_index = source_index
        self._pending: FrameData | None = None
        self._flushRequested.connect(self._flush, Qt.ConnectionType.QueuedConnection)

    @Slot(FrameData)
    def deliver(self, frame_data: FrameData) -> None:
        """Keep *frame_data* as the newest frame and schedule one flush for it."""
        first = self._pending is None
        self._pending = frame_data
        if first:
            self._flushRequested.emit()

    @Slot()
    def _flush(self) -> None:
        frame_data, self._pending = self._pending, None
        if frame_data is not None:
            self.frameReady.emit(self._source_index, frame_data)


class SecondaryFrameRelay(QObject):
    """Marshal secondary-frame callbacks back onto the GUI thread."""

    frameReady = Signal(object, int, int, object)

    def deliver(self, source: PooledVideoSource, generation: int, frame_number: int, frame: FrameData | None) -> None:
        """Deliver a worker completion, including when teardown has already deleted the relay."""
        try:
            self.frameReady.emit(source, generation, frame_number, frame)
        except RuntimeError:
            # A source can finish closing after its session's QObject has been deleted.
            return


class PresentedFrame(NamedTuple):
    """A frame shown on screen: its index in the video and its decoded time."""

    frame: int
    seconds: float


@dataclass(slots=True, eq=False)
class PooledVideoSource:
    """One source-pool entry shared by lanes that reference the same seekable video."""

    source: FileFrameSource
    relay: FrameDeliveryRelay
    source_index: int
    _pending_frame_index: int | None = field(default=None, init=False, repr=False)
    _desired_frame_index: int | None = field(default=None, init=False, repr=False)
    _request_generation: int = field(default=0, init=False, repr=False)

    def reset_frame_requests(self) -> None:
        """Invalidate outstanding completions and clear the latest desired frame."""
        self._request_generation += 1
        self._pending_frame_index = None
        self._desired_frame_index = None

    def request_frame(self, frame_number: int, relay: SecondaryFrameRelay) -> None:
        """Coalesce requests for all lanes sharing this source."""
        self._desired_frame_index = frame_number
        if self._pending_frame_index is None:
            self._request_desired_frame(relay)

    def complete_frame_request(self, generation: int, requested_frame: int, relay: SecondaryFrameRelay) -> bool:
        """Accept only the current desire, scheduling a newer desire when necessary."""
        if generation != self._request_generation or self._pending_frame_index != requested_frame:
            return False
        self._pending_frame_index = None
        if self._desired_frame_index != requested_frame:
            self._request_desired_frame(relay)
            return False
        return True

    def _request_desired_frame(self, relay: SecondaryFrameRelay) -> None:
        if self._desired_frame_index is None:
            return
        frame_number = self._desired_frame_index
        generation = self._request_generation
        self._pending_frame_index = frame_number
        self.source.request_frame_async(
            frame_number,
            lambda frame_data: relay.deliver(self, generation, frame_number, frame_data),
        )
