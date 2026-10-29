"""Resolution field of the live-stream dialog: free text, with the resolutions the camera itself offers listed."""

from __future__ import annotations

from functools import partial

from PySide6.QtGui import QShowEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest, background_jobs
from ax_devil.modules.data_sources.live.camera_resolutions import (
    CameraResolutions,
    ChannelResolutions,
    read_camera_resolutions,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


def _read(connection: DeviceConnection, _job: JobContext[None]) -> CameraResolutions:
    return read_camera_resolutions(connection)


class ResolutionField(QComboBox):
    """Editable resolution that lists what the camera offers for the chosen camera head.

    A new stream starts at the *configured* resolution when the camera offers it, else at the camera's own default.
    Whatever the user types or picks, or ``restore`` sets, is never changed by code, and a camera that cannot be read
    leaves the text as it is: listing is a convenience that never blocks the form.
    """

    def __init__(
        self, configured: str, camera_head: QLineEdit, parent: QWidget, *, jobs: BackgroundJobs | None = None
    ) -> None:
        super().__init__(parent)
        self._configured = configured
        self._camera_head = camera_head
        self._connection = DeviceConnection("", DeviceProtocol.HTTPS)
        self._result: CameraResolutions | None = None
        self._failed = False
        self._chosen = False
        self._shown = False
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setEditable(True)
        self._line = self.lineEdit() or QLineEdit()  # Editable, so it has one.
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)  # type: ignore[arg-type]  # Inline completion would turn a typed "1" into "1920x1080".
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self._request = LatestRequest(self)
        self._jobs = (jobs or background_jobs("discovery")).scope(self)
        self._closed = False
        self.setEditText(configured)
        self._line.textEdited.connect(self._choose)
        self.activated.connect(self._choose)
        camera_head.textChanged.connect(self._on_camera_head_changed)
        self._rebuild()

    def restore(self, saved: str) -> None:
        """Show a saved resolution as it was; the camera's answer never replaces it."""
        self._chosen = True
        self.setEditText(saved)

    def value(self) -> str:
        """Return the resolution as typed or chosen, trimmed."""
        return self.currentText().strip()

    def set_connection(self, connection: DeviceConnection) -> None:
        """List again for another device or login, keeping the text."""
        if connection == self._connection:
            return
        self._request.cancel()
        self._connection = connection
        self._result = None
        self._failed = False
        self._rebuild()

    def cleanup(self) -> None:
        """Drop any request still in flight."""
        self._closed = True
        self._request.close()
        self._jobs.close()

    def showEvent(self, event: QShowEvent) -> None:
        """Start listing the first time the field is shown."""
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            self._load()

    def showPopup(self) -> None:
        """Try again to read the camera when the last attempt gave nothing."""
        self._load()
        super().showPopup()

    def _choose(self) -> None:
        self._chosen = True

    def _load(self) -> None:
        if self._closed or self._request.pending or self._result is not None or not self._connection.host:
            return
        self._failed = False
        self._request.submit(
            self._jobs,
            "load resolutions",
            partial(_read, self._connection),
            on_result=self._on_loaded,
            on_error=self._on_failed,
        )
        self._rebuild()

    def _on_loaded(self, result: CameraResolutions) -> None:
        self._result = result
        self._rebuild()
        if not self._chosen:
            self.setEditText(self._channel().preferred(self._configured))

    def _on_failed(self, error: Exception) -> None:
        logger.debug(f"Could not read resolutions from {self._connection.host}: {error}")
        self._failed = True
        self._rebuild()

    def _on_camera_head_changed(self) -> None:
        self._rebuild()
        if self._result is not None and not self._chosen:
            self.setEditText(self._channel().preferred(self._configured))

    def _channel(self) -> ChannelResolutions:
        text = self._camera_head.text().strip() or "1"
        if self._result is None or not text.isdigit() or int(text) < 1:
            return ChannelResolutions((), "")
        return self._result.channel(int(text))

    def _status(self) -> str:
        if not self._connection.host:
            return "Enter a device host to list resolutions"
        if self._failed:
            return "Could not read resolutions"
        if self._result is not None:
            return "Camera lists no resolutions"
        return "Loading resolutions…"

    def _rebuild(self) -> None:
        """List the channel's resolutions, or one disabled row saying why there are none; the text stays as it is."""
        text = self.currentText()
        resolutions = self._channel().resolutions
        blocked = (self.blockSignals(True), self._line.blockSignals(True))
        self.clear()
        for resolution in resolutions or (self._status(),):
            self._model.appendRow(QStandardItem(resolution))
        if not resolutions:
            self._model.item(0).setEnabled(False)
        self.setCurrentIndex(-1)
        self.setEditText(text)
        self.blockSignals(blocked[0])
        self._line.blockSignals(blocked[1])
