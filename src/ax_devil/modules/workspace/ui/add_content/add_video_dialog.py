"""Dialog for adding a local video file with any number of overlay files."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import StatusColor, show_status
from ax_devil.modules.chrome.tokens import Height, Space, keep_minimum_chars, reserve_line
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.intake import VIDEO_FILE_SUFFIXES, WorkspaceIntake
from ax_devil.modules.workspace.core.items import OverlayChoice, VideoFileSelection, VideoItem
from ax_devil.modules.workspace.ui.plugin_intake import default_workspace_intake

logger = get_logger(__name__)


def _file_row(edit: QLineEdit, browse: Callable[[], None], what: str) -> QWidget:
    """Return *edit* with a browse button beside it."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(Space.XS)
    keep_minimum_chars(edit, 36)
    layout.addWidget(edit, 1)
    layout.addWidget(BrowseButton(what, browse))
    return row


class OverlayFileRow(QWidget):
    """One overlay file and the data handler that reads it, with a button that asks to remove the row.

    Choosing a file picks the only handler that may read it and drops a choice that cannot.
    """

    changed = Signal()
    remove_requested = Signal()

    def __init__(self, intake: WorkspaceIntake, initial: OverlayChoice | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._intake = intake

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Optional")
        self._path_edit.setAccessibleName("Overlay file")

        self._handler_combo = QComboBox()
        self._handler_combo.setAccessibleName("Data handler")
        self._handler_combo.addItem("(None)", None)
        for decoder in intake.file_decoder_options():
            self._handler_combo.addItem(decoder.display_name, decoder.handler_type)

        remove = QToolButton()
        remove.setIcon(Icon.REMOVE.icon())
        remove.setToolTip("Remove this overlay")
        remove.setAccessibleName("Remove overlay")
        remove.setAutoRaise(True)
        remove.clicked.connect(self.remove_requested)
        follow_appearance(remove, lambda: remove.setFixedSize(Height.CONTROL.px, Height.CONTROL.px))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)
        file_layout = QHBoxLayout()
        file_layout.setSpacing(Space.XS)
        file_layout.addWidget(_file_row(self._path_edit, self._browse, "an overlay file"), 1)
        file_layout.addWidget(remove)
        layout.addLayout(file_layout)
        layout.addWidget(self._handler_combo)

        self._path_edit.textChanged.connect(self._on_path_changed)
        self._handler_combo.currentIndexChanged.connect(self.changed)
        if initial is not None:
            self._path_edit.setText(str(initial.path))
            if initial.decoder is not None:
                self._select_handler(initial.decoder)
        self._handler_combo.setEnabled(bool(self._path()))

    def choice(self) -> OverlayChoice | None:
        """Return the file and handler entered, or None while no file is entered."""
        path = self._path()
        return OverlayChoice(Path(path), self._handler_combo.currentData()) if path else None

    def problem(self) -> str:
        """Return why this row cannot be accepted yet, or an empty string when it can."""
        path = self._path()
        if not path:
            return ""
        if not Path(path).is_file():
            return f"Overlay file not found: {Path(path).name}"
        if self._handler_combo.currentData() is None:
            return f"Choose the data handler that reads {Path(path).name}."
        return ""

    def focus_path(self) -> None:
        """Put the cursor in the file field."""
        self._path_edit.setFocus()

    def _path(self) -> str:
        return self._path_edit.text().strip()

    def _browse(self) -> None:
        suffixes = sorted(
            {suffix for option in self._intake.file_decoder_options() for suffix in option.file_extensions}
        )
        patterns = " ".join(f"*{suffix}" for suffix in suffixes)
        filters = f"Data Files ({patterns});;All Files (*)" if patterns else "All Files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Select Overlay File", "", filters)
        if path:
            self._path_edit.setText(path)

    def _on_path_changed(self, text: str) -> None:
        """Pick the only decoder that may read the file, and drop a choice that cannot read it."""
        path = text.strip()
        candidates = [option.handler_type for option in self._intake.file_decoder_options_for(Path(path))]
        if path and len(candidates) == 1:
            self._select_handler(candidates[0])
        elif self._handler_combo.currentData() not in candidates:
            self._handler_combo.setCurrentIndex(0)
        self._handler_combo.setEnabled(bool(path))
        self.changed.emit()

    def _select_handler(self, handler_type: str) -> None:
        index = self._handler_combo.findData(handler_type)
        self._handler_combo.setCurrentIndex(max(0, index))


class AddVideoDialog(BaseDialog):
    """Collect a video file, any number of overlay files with their data handlers, and an optional display name.

    Each overlay file becomes a lane to compare. OK stays disabled until the selection can be opened; the reason is
    shown inline under the form.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        initial: VideoFileSelection | None = None,
        *,
        existing: VideoItem | None = None,
    ) -> None:
        super().__init__(parent, title="Edit Video" if existing is not None else "Add Video")
        self._existing = existing
        if existing is not None:
            initial = VideoFileSelection(
                existing.video, tuple(OverlayChoice(o.path, o.decoder) for o in existing.overlays)
            )
        self._result: VideoItem | None = None
        self._intake = default_workspace_intake()
        self._overlay_rows: list[OverlayFileRow] = []
        self._setup_form()
        self._ok_button, _ = self.add_standard_buttons()
        if existing is not None:
            self._ok_button.setText("Save")
            self._name_edit.setText(existing.label)
        if initial is not None:
            self._video_path_edit.setText(str(initial.video))
        for choice in initial.overlays if initial is not None else (None,):
            self._add_overlay_row(choice)
        self._refresh()
        if existing is not None:
            self._name_edit.setFocus()
            self._name_edit.selectAll()

    def _setup_form(self) -> None:
        form = QWidget()
        form_layout = FormLayout(form)

        self._video_path_edit = QLineEdit()
        self._video_path_edit.setPlaceholderText("Required")
        self._video_path_edit.textChanged.connect(self._refresh)
        form_layout.addRow("Video File", _file_row(self._video_path_edit, self._browse_video, "a video file"))

        overlays = QWidget()
        overlays_layout = QVBoxLayout(overlays)
        overlays_layout.setContentsMargins(0, 0, 0, 0)
        overlays_layout.setSpacing(Space.S)
        self._overlay_list = QVBoxLayout()
        self._overlay_list.setSpacing(Space.S)
        overlays_layout.addLayout(self._overlay_list)
        add_overlay = SmallButton("Add overlay", Icon.ADD)
        add_overlay.clicked.connect(lambda: self._add_overlay_row().focus_path())
        overlays_layout.addWidget(add_overlay, 0, Qt.AlignmentFlag.AlignLeft)
        form_layout.addRow("Overlays", overlays)

        self._name_edit = QLineEdit()
        form_layout.addRow("Name", self._name_edit)

        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        show_status(self._message_label, StatusColor.ERROR)  # It only ever shows problems.
        reserve_line(self._message_label)
        form_layout.addRow(self._message_label)

        self.add_content_widget(form)

    def _browse_video(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in VIDEO_FILE_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(self, "Select Video File", "", f"Video Files ({patterns});;All Files (*)")
        if path:
            self._video_path_edit.setText(path)

    def _add_overlay_row(self, initial: OverlayChoice | None = None) -> OverlayFileRow:
        """Add a row for one more overlay file, showing *initial* when given, and return it."""
        row = OverlayFileRow(self._intake, initial)
        row.changed.connect(self._refresh)
        row.remove_requested.connect(lambda: self._remove_overlay_row(row))
        self._overlay_rows.append(row)
        self._overlay_list.addWidget(row)
        self._grow_to_rows()
        return row

    def _remove_overlay_row(self, row: OverlayFileRow) -> None:
        self._overlay_rows.remove(row)
        self._overlay_list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._grow_to_rows()
        self._refresh()

    def _grow_to_rows(self) -> None:
        """Refit an open dialog to its rows, once Qt has laid them out, so they do not start a scroll bar."""
        QTimer.singleShot(0, self, self._fit_to_rows)

    def _fit_to_rows(self) -> None:
        self._scroll_area.updateGeometry()  # The scroll area's hint changed, which its dialog's layout cannot see.
        self._refit_when_laid_out()

    def _item(self) -> VideoItem:
        """Return the Video Item described by the current form."""
        choices = (choice for row in self._overlay_rows if (choice := row.choice()) is not None)
        selection = VideoFileSelection(Path(self._video_path_edit.text().strip()), tuple(choices))
        item = selection.to_item(self._name_edit.text().strip())
        return replace(item, id=self._existing.id) if self._existing is not None else item

    def _problem(self) -> str:
        """Return why the form cannot be accepted yet, or an empty string when it can."""
        video = self._video_path_edit.text().strip()
        if not video:
            return "Choose a video file to continue."
        if not Path(video).is_file():
            return "Video file not found."
        return next((problem for row in self._overlay_rows if (problem := row.problem())), "")

    def _refresh(self) -> None:
        """Update OK, the name placeholder, and the inline message."""
        video = self._video_path_edit.text().strip()
        self._name_edit.setPlaceholderText(Path(video).name if video else "Optional")
        problem = self._problem()
        self._message_label.setText(problem)
        self._ok_button.setEnabled(not problem)

    def accept(self) -> None:
        """Accept only a complete selection."""
        if self._problem():
            return
        item = self._item()
        try:
            self._intake.create_seekable_video(
                video_path=item.video, display_name=item.default_name, overlays=item.overlays
            )
        except ValueError as exc:
            self._message_label.setText(str(exc))
            return
        self._result = item
        super().accept()

    def get_result(self) -> VideoItem | None:
        """Return the Video Item, or None if the dialog was cancelled."""
        return self._result
