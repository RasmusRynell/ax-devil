"""Recording controls for the diagnostics window."""

from datetime import datetime
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ax_devil.modules.diagnostics.trace_recorder import TraceRecorder
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class TraceControls(QWidget):
    """Start, stop, and export a period of application execution."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.recorder = TraceRecorder()
        self._started_at = 0.0
        layout = QHBoxLayout(self)
        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self._toggle_recording)
        self.save_button = QPushButton("Save trace")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        self.status = QLabel("Sample Python stacks at a target 100 Hz and save the recording as JSON.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.record_button)
        layout.addWidget(self.save_button)
        layout.addWidget(self.status, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_elapsed)

    def _toggle_recording(self) -> None:
        try:
            if self.recorder.recording:
                self.recorder.stop()
                self._timer.stop()
                self.record_button.setText("Start recording")
                self.save_button.setEnabled(True)
                self.status.setText("Recording stopped. Save the trace before starting another recording.")
                self._save()
            else:
                self.recorder.start()
                self._started_at = monotonic()
                self.record_button.setText("Stop recording")
                self.save_button.setEnabled(False)
                self._timer.start()
                self._update_elapsed()
        except Exception as error:
            logger.exception(f"Trace recording failed: {error}")
            QMessageBox.warning(self, "Trace recording", str(error))

    def _update_elapsed(self) -> None:
        if self.recorder.failure is not None:
            self.recorder.stop()
            self._timer.stop()
            self.record_button.setText("Start recording")
            self.save_button.setEnabled(True)
            self.status.setText(f"Recording stopped early: {self.recorder.failure}. Retained samples can be saved.")
            return
        self.status.setText(
            f"Sampling • {monotonic() - self._started_at:.1f}s • {self.recorder.sample_count:,} snapshots retained"
        )

    def _save(self) -> None:
        filename = f"ax-devil-trace-{datetime.now():%Y%m%d-%H%M%S}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save performance trace", filename, "Trace JSON (*.json)")
        if not path:
            return
        try:
            self.recorder.save(Path(path))
            self.status.setText(f"Saved {Path(path).name}.")
            self.status.setToolTip(path)
        except Exception as error:
            logger.exception(f"Trace export failed: {error}")
            QMessageBox.warning(self, "Trace export", f"{error}\nThe recording is retained; try Save trace again.")

    def cleanup(self) -> None:
        """Release timers and stop capture when the owning window closes."""
        self._timer.stop()
        self.recorder.cleanup()
