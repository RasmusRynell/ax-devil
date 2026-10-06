"""Video export options and progress dialog."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.export.encoder import CompressionPreset
from ax_devil.modules.video_viewer.export.export_job import ExportJob, ExportLane

logger = get_logger(__name__)


class ExportDialog(BaseDialog):
    """Choose export options, display progress, and forward cancellation to the active job."""

    def __init__(
        self,
        lanes: Sequence[ExportLane],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, title="Export Video with Overlays")

        self._lanes = tuple(lanes)
        self._lane_checks: list[QCheckBox] = []
        self._job: ExportJob | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._options_widget = QWidget()
        form_layout = FormLayout(self._options_widget)

        if len(self._lanes) > 1:
            lanes = QWidget()
            lanes_layout = QVBoxLayout(lanes)
            lanes_layout.setContentsMargins(0, 0, 0, 0)
            lanes_layout.setSpacing(2)
            for lane in self._lanes:
                check = QCheckBox(lane.name)
                check.setChecked(True)
                check.toggled.connect(self._update_export_enabled)
                lanes_layout.addWidget(check)
                self._lane_checks.append(check)
            form_layout.addRow("Lanes:", lanes)

        self._quality_combo = QComboBox()
        self._presets = CompressionPreset.all_presets()
        for preset in self._presets:
            self._quality_combo.addItem(preset.label)
        self._quality_combo.setCurrentIndex(self._presets.index(CompressionPreset.default()))
        form_layout.addRow("Quality:", self._quality_combo)

        self.add_content_widget(self._options_widget)

        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        progress_layout.addWidget(self._progress)

        self._status_label = QLabel("Ready to export")
        self._status_label.setWordWrap(True)
        progress_layout.addWidget(self._status_label)
        self.add_content_widget(self._progress_widget)
        self._progress_widget.hide()

        self._content_layout.addStretch()

        self._export_button = self.add_button("Export", callback=self._start_export, is_default=True)
        self._cancel_button = self.add_button("Cancel", callback=self._cancel)
        self._cancel_button.setEnabled(False)

    def _selected_lanes(self) -> list[ExportLane]:
        if not self._lane_checks:
            return list(self._lanes)
        return [lane for lane, check in zip(self._lanes, self._lane_checks) if check.isChecked()]

    @Slot()
    def _update_export_enabled(self) -> None:
        self._export_button.setEnabled(bool(self._selected_lanes()))

    @Slot()
    def _start_export(self) -> None:
        """Ask where the file should land, then export."""
        output_path = _prompt_export_path(self, f"{Path(self._selected_lanes()[0].name).stem}.mp4")
        if output_path is not None:
            self._export(output_path)

    def _export(self, output_path: Path) -> None:
        """Run the export pipeline into output_path."""
        job = ExportJob(
            self._selected_lanes(), output_path, compression=self._presets[self._quality_combo.currentIndex()]
        )
        self._job = job
        self._options_widget.hide()
        self._progress_widget.show()
        self._export_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        for check in self._lane_checks:
            check.setEnabled(False)
        self._status_label.setText("Exporting...")

        try:
            QApplication.processEvents()
            completed = job.run(self._update_progress)
        except Exception as e:
            logger.error(f"Export failed: {e}", exc_info=True)
            self._show_done(f"Error: {e}", accept=False)
            return
        finally:
            self._job = None

        if not completed:
            self._show_done("Cancelled", accept=False)
        else:
            self._show_done(f"Done: {output_path}", accept=True)

    def _update_progress(self, current: int, total: int) -> None:
        """Update progress bar and pump the event loop."""
        pct = int((current + 1) / total * 100)
        self._progress.setValue(pct)
        self._status_label.setText(f"Frame {current + 1} / {total}")
        QApplication.processEvents()

    def _show_done(self, message: str, *, accept: bool) -> None:
        """Switch UI to done state."""
        self._status_label.setText(message)
        self._cancel_button.setEnabled(False)
        self._export_button.setText("Close")
        self._export_button.setEnabled(True)
        self._export_button.clicked.disconnect(self._start_export)
        self._export_button.clicked.connect(self.accept if accept else self.reject)

    def reject(self) -> None:
        """Treat Escape and window close as cancellation while an export is running."""
        if self._job is not None:
            self._cancel()
            return
        super().reject()

    @Slot()
    def _cancel(self) -> None:
        """Request cancellation."""
        if self._job is not None:
            self._job.cancel()


def _prompt_export_path(parent: QWidget, suggested_name: str) -> Path | None:
    """Show a save-file dialog and return the chosen .mp4 path, or None if cancelled."""
    dialog = QFileDialog(parent, "Export Video", suggested_name, "MP4 Video (*.mp4)")
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setDefaultSuffix("mp4")
    try:
        if not dialog.exec():
            return None
        result = Path(dialog.selectedFiles()[0])
        return result if result.suffix.lower() == ".mp4" else result.with_name(f"{result.name}.mp4")
    finally:
        dialog.deleteLater()
