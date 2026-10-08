"""Qt adapters for the synchronization engine."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.logging_config import get_logger

from .engine import StreamSync, SyncResult

logger = get_logger(__name__)


class QtStreamSync(QObject):
    """Qt wrapper around StreamSync core.

    Provides Qt signal interface while keeping all sync logic in the standalone, testable core.
    """

    # Signal emitted when synchronized frame is ready
    syncReady = Signal(object)  # SyncResult[FrameType, OverlayType]

    def __init__(self, object_name: str, parent: QObject | None = None, **sync_config: Any):
        super().__init__(parent)

        # Create the pure Python sync core
        self._sync_core: StreamSync[Any, Any] = StreamSync(object_name=object_name, **sync_config)
        self._sync_core.set_output_callback(self._on_sync_result)

    def push_frame(self, frame_data: Any, capture_time: float) -> None:
        """Push frame data to sync core."""
        self._sync_core.push_frame(frame_data, capture_time)

    def push_overlay(self, overlay_data: Any, capture_time: float) -> None:
        """Push overlay data to sync core."""
        self._sync_core.push_overlay(overlay_data, capture_time)

    def reset(self) -> None:
        """Clear all buffered data in the sync core."""
        self._sync_core.reset()

    def _on_sync_result(self, result: SyncResult[Any, Any]) -> None:
        """Callback from sync core - emit Qt signal."""
        self.syncReady.emit(result)

    def cleanup(self) -> None:
        """Detach from the sync core, release its diagnostics, and schedule deletion."""
        # Break the bound-method reference from the core back to this Qt object.
        self._sync_core.set_output_callback(lambda _result: None)
        self._sync_core.cleanup()
        self.deleteLater()
