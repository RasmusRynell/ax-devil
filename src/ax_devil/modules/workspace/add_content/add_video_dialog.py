"""Dialog for adding a local video file with optional overlay."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.intake import VIDEO_FILE_SUFFIXES, default_workspace_intake
from ax_devil.modules.workspace.startup_request import VideoFileStartup

logger = get_logger(__name__)


class AddVideoDialog(BaseDialog):
    """Collect a video file, an optional overlay file with its data handler, and an optional display name.

    OK stays disabled until the selection can be opened; the reason is shown inline under the form.
    """

    def __init__(self, parent: QWidget | None = None, initial: VideoFileStartup | None = None) -> None:
        super().__init__(parent, title="Add Video")
        self._result: VideoFileStartup | None = None
        self._intake = default_workspace_intake()
        self._setup_form()
        self._ok_button, _ = self.add_standard_buttons()
        if initial is not None:
            self._video_path_edit.setText(str(initial.video_path))
            self._overlay_path_edit.setText(str(initial.overlay_path or ""))
            if initial.handler_type is not None:
                self._select_handler(initial.handler_type)
            self._name_edit.setText(initial.display_name or "")
        self._refresh()

    def _setup_form(self) -> None:
        form = QWidget()
        form_layout = FormLayout(form)

        self._video_path_edit = QLineEdit()
        self._video_path_edit.setPlaceholderText("Required")
        self._video_path_edit.textChanged.connect(self._refresh)
        form_layout.addRow("Video File", self._file_row(self._video_path_edit, self._browse_video, "a video file"))

        self._overlay_path_edit = QLineEdit()
        self._overlay_path_edit.setPlaceholderText("Optional")
        self._overlay_path_edit.textChanged.connect(self._on_overlay_changed)
        form_layout.addRow(
            "Overlay File", self._file_row(self._overlay_path_edit, self._browse_overlay, "an overlay file")
        )

        # Handler type dropdown (populated from decoder plugins)
        self._handler_combo = QComboBox()
        self._handler_combo.addItem("(None)", None)
        for decoder in self._intake.file_decoder_options():
            self._handler_combo.addItem(decoder.display_name, decoder.handler_type)
        self._handler_combo.currentIndexChanged.connect(self._refresh)
        form_layout.addRow("Data Handler", self._handler_combo)

        self._name_edit = QLineEdit()
        form_layout.addRow("Display Name", self._name_edit)

        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._message_label.setMinimumHeight(self._message_label.fontMetrics().height())
        form_layout.addRow(self._message_label)

        self.add_content_widget(form)

    @staticmethod
    def _file_row(edit: QLineEdit, browse: Callable[[], None], what: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        edit.setMinimumWidth(edit.fontMetrics().averageCharWidth() * 36)
        layout.addWidget(edit, 1)
        layout.addWidget(BrowseButton(what, browse))
        return row

    def _browse_video(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in VIDEO_FILE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(self, "Select Video File", "", f"Video Files ({patterns});;All Files (*)")
        if path:
            self._video_path_edit.setText(path)

    def _browse_overlay(self) -> None:
        suffixes = sorted(
            {suffix for option in self._intake.file_decoder_options() for suffix in option.file_extensions}
        )
        patterns = " ".join(f"*{suffix}" for suffix in suffixes)
        filters = f"Data Files ({patterns});;All Files (*)" if patterns else "All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Overlay File", "", filters)
        if path:
            self._overlay_path_edit.setText(path)

    def _on_overlay_changed(self, text: str) -> None:
        """Pick the only decoder that may read the overlay file, and drop a choice that cannot read it."""
        overlay = text.strip()
        candidates = [option.handler_type for option in self._intake.file_decoder_options_for(Path(overlay))]
        if overlay and len(candidates) == 1:
            self._select_handler(candidates[0])
        elif self._handler_combo.currentData() not in candidates:
            self._handler_combo.setCurrentIndex(0)
        self._refresh()

    def _select_handler(self, handler_type: str) -> None:
        index = self._handler_combo.findData(handler_type)
        if index >= 0:
            self._handler_combo.setCurrentIndex(index)

    def _request(self) -> VideoFileStartup:
        """Return the open request described by the current form."""
        video = self._video_path_edit.text().strip()
        overlay = self._overlay_path_edit.text().strip()
        return VideoFileStartup(
            video_path=Path(video),
            overlay_path=Path(overlay) if overlay else None,
            handler_type=self._handler_combo.currentData() if overlay else None,
            display_name=self._name_edit.text().strip() or None,
        )

    def _problem(self) -> str:
        """Return why the form cannot be accepted yet, or an empty string when it can."""
        video = self._video_path_edit.text().strip()
        overlay = self._overlay_path_edit.text().strip()
        if not video:
            return "Choose a video file to continue."
        if not Path(video).is_file():
            return "Video file not found."
        if overlay and not Path(overlay).is_file():
            return "Overlay file not found."
        if overlay and self._handler_combo.currentData() is None:
            return "Choose the data handler that reads the overlay file."
        return ""

    def _refresh(self) -> None:
        """Update OK, the handler choice, the name placeholder, and the inline message."""
        video = self._video_path_edit.text().strip()
        self._name_edit.setPlaceholderText(Path(video).name if video else "Optional")
        self._handler_combo.setEnabled(bool(self._overlay_path_edit.text().strip()))
        problem = self._problem()
        self._message_label.setText(problem)
        self._ok_button.setEnabled(not problem)

    def accept(self) -> None:
        """Accept only a complete selection."""
        if self._problem():
            return
        self._result = self._request()
        super().accept()

    def get_result(self) -> VideoFileStartup | None:
        """Return the video open request, or None if the dialog was cancelled."""
        return self._result
