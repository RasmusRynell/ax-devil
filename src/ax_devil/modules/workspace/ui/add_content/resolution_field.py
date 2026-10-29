"""Resolution field of the live-stream dialog: free text, with the resolutions the camera itself offers listed."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest, background_jobs
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.data_sources.live.camera_resolutions import (
    CameraResolutions,
    ChannelResolutions,
    read_camera_resolutions,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_DEBOUNCE_MS = 600  # Typing a host passes through addresses that are not the camera.


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
        self._problem = ""  # The status row after an attempt that gave no usable list.
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
        # Editable combos skip the padded menu-style popup that the other combos get.
        self.view().setStyleSheet(f"QListView::item {{ padding: {Space.S}px {Space.XL}px; }}")
        self._request = LatestRequest(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._load)
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
        """List again for another device or login, keeping the text; a shown field waits out typing first."""
        if connection == self._connection:
            return
        self._request.cancel()
        self._connection = connection
        self._result = None
        self._problem = ""
        if self.isVisible() and connection.host:
            self._timer.start()
        else:
            self._timer.stop()
        self._rebuild()

    def cleanup(self) -> None:
        """Drop any request still in flight."""
        self._closed = True
        self._timer.stop()
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
        self._timer.stop()
        self._problem = ""
        self._request.submit(
            self._jobs,
            "load resolutions",
            partial(_read, self._connection),
            on_result=self._on_loaded,
            on_error=self._on_failed,
        )
        self._rebuild()

    def _on_loaded(self, result: CameraResolutions) -> None:
        if result.params:
            self._result = result
        else:  # An empty or error answer lists nothing worth keeping; the next popup asks again.
            self._problem = "Camera lists no resolutions"
        self._rebuild()
        self._apply_preferred()

    def _on_failed(self, error: Exception) -> None:
        logger.debug(f"Could not read resolutions from {self._connection.host}: {error}")
        self._problem = "Could not read resolutions"
        self._rebuild()

    def _on_camera_head_changed(self) -> None:
        self._rebuild()
        self._apply_preferred()

    def _apply_preferred(self) -> None:
        """Start on what the camera offers for the channel, until the user has typed or chosen."""
        if self._chosen or self._result is None:
            return
        preferred = self._channel().preferred(self._configured)
        if preferred != self.currentText():
            self.setEditText(preferred)

    def _channel(self) -> ChannelResolutions:
        text = self._camera_head.text().strip() or "1"
        if self._result is None or not text.isdigit() or int(text) < 1:
            return ChannelResolutions((), "")
        return self._result.channel(int(text))

    def _status(self) -> str:
        if not self._connection.host:
            return "Enter a device host to list resolutions"
        if self._request.pending or self._timer.isActive():
            return "Loading resolutions…"
        if self._problem:
            return self._problem
        return "Camera lists no resolutions" if self._result is not None else "Resolutions not read yet"

    def _rebuild(self) -> None:
        """List the channel's resolutions, or one disabled row saying why there are none.

        The combo is not told, so it leaves the editor alone: its text, caret, selection and undo are the user's.
        """
        resolutions = self._channel().resolutions
        self._model.blockSignals(True)
        self._model.removeRows(0, self._model.rowCount())
        for resolution in resolutions or (self._status(),):
            self._model.appendRow(QStandardItem(resolution))
        if not resolutions:
            self._model.item(0).setEnabled(False)
        self._model.blockSignals(False)
        self.view().reset()
        if self.view().isVisible():  # An open popup keeps the height of the rows it opened with.
            super().showPopup()
