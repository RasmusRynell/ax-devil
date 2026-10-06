"""Base classes for data sources.

Clean separation of Worker (threading/execution) from DataSource (capability) interfaces.
"""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from ax_devil.modules.filtering import FilterConfig

from PySide6.QtCore import QMutex, QObject, QThread, QWaitCondition, Signal, Slot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

###############################################################################
# 0 ── Metaclass to resolve QObject + ABC conflicts
###############################################################################


class QObjectABCMeta(ABCMeta, type(QObject)):
    """Metaclass that combines QObject's metaclass with ABC's metaclass."""


###############################################################################
# 1 ── Execution model: Worker handles threading and lifecycle
###############################################################################


class Worker(QThread):
    """Handles threading, lifecycle, and error management for data sources."""

    sourceError = Signal(str)
    """Emitted with a reason when production fails without an automatic retry."""
    sourceConnected = Signal()
    """Emitted when the transport has connected to its peer."""
    sourceReconnecting = Signal(str)
    """Emitted with a reason when a connection attempt failed and the worker will try again."""
    sourceFinished = Signal()

    def __init__(self, source_id: str = "default") -> None:
        super().__init__()
        self.source_id = source_id
        self._running = False
        self._paused = False
        self._pause_condition = QWaitCondition()
        self._mutex = QMutex()
        try:
            _id = id(self)
            _sid = source_id
            self.destroyed.connect(
                lambda _=None, _id=_id, _sid=_sid: logger.debug(f"Worker.destroyed id={_id} source_id={_sid}")
            )
        except Exception:
            pass

    def play(self) -> bool:
        """Start the worker."""
        if self._running:
            self._mutex.lock()
            was_paused = self._paused
            self._paused = False
            if was_paused:
                self.on_resume()
                self._pause_condition.wakeAll()
            self._mutex.unlock()
            return True

        self._running = True
        self._paused = False

        if not self.isRunning():
            try:
                self.start()
                return True
            except Exception as e:
                self.sourceError.emit(f"Failed to start: {str(e)}")
                return False
        return True

    def on_resume(self) -> None:
        """Hook called when transitioning from paused to playing."""

    def pause(self) -> None:
        """Pause the worker."""
        self._mutex.lock()
        self._paused = True
        self._mutex.unlock()

    def stop(self) -> None:
        """Stop the worker."""
        self._mutex.lock()
        self._running = False
        self._paused = False
        self._pause_condition.wakeAll()
        self._mutex.unlock()

    def is_playing(self) -> bool:
        """Check if worker is actively running."""
        return self._running and not self._paused

    def run(self) -> None:
        """Main thread loop with pause support."""
        logger.debug(f"[{self.source_id}] Worker started, running={self._running}")

        while self._running:
            self._mutex.lock()
            if self._paused:
                self._pause_condition.wait(self._mutex)
                self._mutex.unlock()
                continue
            self._mutex.unlock()

            try:
                if not self.run_loop():
                    break
            except Exception as e:
                logger.error(f"[{self.source_id}] Processing error: {str(e)}")
                self.sourceError.emit(f"Processing error: {str(e)}")
                break

        logger.debug(f"[{self.source_id}] Worker finished")
        self.sourceFinished.emit()

    @abstractmethod
    def run_loop(self) -> bool:
        """Process one unit of work.

        Return False to stop the worker.
        """


###############################################################################
# 2 ── Data source interfaces: Define shared lifecycle plus specific capabilities
###############################################################################


class DataSource(QObject, metaclass=QObjectABCMeta):
    """Shared lifecycle contract for all media and overlay sources."""

    sourceError = Signal(str)
    """Emitted with a reason when production fails without an automatic retry."""
    sourceConnected = Signal()
    """Emitted when a live transport has connected to its peer."""
    sourceReconnecting = Signal(str)
    """Emitted with a reason when a live connection attempt failed and the source will try again."""
    _retiring: ClassVar[set[DataSource]] = set()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._owned_worker: Worker | None = None
        self._delete_requested = False
        try:
            _id = id(self)
            _cls = self.__class__.__name__
            self.destroyed.connect(
                lambda _=None, _id=_id, _cls=_cls: logger.debug(f"{_cls}.destroyed id={_id} class={_cls}")
            )
        except Exception:
            pass

    def _own_worker(self, worker: Worker) -> None:
        worker.setParent(self)
        self._owned_worker = worker
        worker.finished.connect(self._finish_delete)

    def deleteLater(self) -> None:
        """Release this source and its worker after production has finished."""
        if self._delete_requested:
            return
        self._delete_requested = True
        self._retiring.add(self)
        if self._owned_worker is None or not self._owned_worker.isRunning():
            self._finish_delete()

    @Slot()
    def _finish_delete(self) -> None:
        if self._delete_requested:
            super().deleteLater()
            self._retiring.discard(self)

    @abstractmethod
    def play(self) -> bool:
        """Start source production."""

    @abstractmethod
    def pause(self) -> None:
        """Pause source production."""

    @abstractmethod
    def stop(self) -> None:
        """Stop source production."""

    @abstractmethod
    def wait(self, timeout: int = 2000) -> bool:
        """Wait for source production to complete."""

    def cleanup_posted_events(self) -> None:
        from PySide6 import QtCore

        QtCore.QCoreApplication.removePostedEvents(self)

    # No __del__ on QObject-derived classes (unsafe at interpreter shutdown)


class FrameSource(DataSource):
    """Interface for sources that provide frame data."""

    frameReady = Signal(FrameData)


class OverlaySource(DataSource):
    """Interface for sources that provide overlay data."""

    overlayReady = Signal(OverlayData)

    def emit_overlay(self, overlay_data: OverlayData) -> None:
        """Thread-safe method to emit overlay data."""
        self.overlayReady.emit(overlay_data)

    @property
    @abstractmethod
    def handler_type(self) -> str:
        """Return the handler type identifier for this overlay source."""

    @abstractmethod
    def get_filter_config(self) -> FilterConfig | None:
        """Return filter configuration for this overlay source or None if no filtering is supported."""


###############################################################################
# 3 ── Capability axis: Seekable interface
###############################################################################


class Seekable(ABC):
    """Interface for sources that support seeking to specific positions."""

    @abstractmethod
    def jump_to(self, frame_number: int) -> None:
        """Jump to specific frame number."""

    @abstractmethod
    def get_total_frames(self) -> int:
        """Get total number of frames."""

    @abstractmethod
    def get_current_frame(self) -> int:
        """Get current frame position."""

    def step_delta(self, delta: int) -> None:
        """Step forward/backward by specified number of frames."""
        self.jump_to(self.get_current_frame() + delta)


###############################################################################
# 4 ── Composite and pull capability interfaces
###############################################################################


class SeekableFrameSource(FrameSource, Seekable):
    """Frame source that supports seeking."""


class OverlayLookup(Protocol):
    """Pull-based lookup capability for overlays identified by video frame."""

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        """Get a matched overlay, or the latest past sample when allow_previous is enabled."""
        ...
