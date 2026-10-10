"""Settings widget for the folder-pair playlist resolver."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import PlaylistResolverWidget, get_file_decoder_definitions
from ax_devil.modules.settings.logging_config import get_logger

from .resolver import FolderPairMatch, discover_folder_pairs

logger = get_logger(__name__)


class FolderPairSettingsWidget(PlaylistResolverWidget):
    """Edit and submit the video folder, overlay folder, and decoder of a folder-pair playlist."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._matches: list[FolderPairMatch] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.L)
        form = FormLayout()
        layout.addLayout(form)

        self._videos_input = self._add_directory_row(form, "Videos:", "Path to folder containing video files")
        self._overlays_input = self._add_directory_row(form, "Overlays:", "Path to folder containing overlay files")
        self._videos_input.textChanged.connect(self._clear_matches)
        self._overlays_input.textChanged.connect(self._clear_matches)

        self._decoder_combo = QComboBox()
        self._decoder_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._decoder_combo.addItem("Select a decoder...", None)
        for decoder in get_file_decoder_definitions():
            self._decoder_combo.addItem(f"{decoder.display_name} ({decoder.handler_type})", decoder.handler_type)
        form.addRow("Decoder", self._decoder_combo)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._scan_pairs)
        action_row.addWidget(self._scan_btn)
        self._load_btn = QPushButton("Load Playlist")
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load)
        action_row.addWidget(self._load_btn)
        layout.addLayout(action_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def _add_directory_row(self, layout: FormLayout, label: str, placeholder: str) -> QLineEdit:
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        path_input = QLineEdit()
        path_input.setPlaceholderText(placeholder)
        row.addWidget(path_input, 1)
        row.addWidget(BrowseButton("a folder", lambda: self._browse_directory(path_input)))
        layout.addRow(label, field)
        return path_input

    def _browse_directory(self, path_input: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if path:
            path_input.setText(path)

    def _scan_pairs(self) -> None:
        videos_text = self._videos_input.text().strip()
        overlays_text = self._overlays_input.text().strip()
        if not videos_text or not overlays_text:
            self._matches = []
            self._load_btn.setEnabled(False)
            self._status_label.setText("Select video and overlay folders.")
            return

        videos_dir = Path(videos_text)
        overlays_dir = Path(overlays_text)
        try:
            self._matches = discover_folder_pairs(videos_dir, overlays_dir)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(f"Failed to scan folder-pair dataset: {exc}")
            self._matches = []
            self._load_btn.setEnabled(False)
            self._status_label.setText(f"Scan failed: {exc}")
            return

        if not self._matches:
            self._load_btn.setEnabled(False)
            self._status_label.setText("No matched video/overlay pairs found.")
            return

        self._load_btn.setEnabled(True)
        self._status_label.setText(f"Found {len(self._matches)} matched pair(s).")

    def _clear_matches(self) -> None:
        self._matches = []
        self._load_btn.setEnabled(False)
        self._status_label.setText("")

    def _on_load(self) -> None:
        handler_type = self._decoder_combo.currentData()
        if not isinstance(handler_type, str) or not handler_type:
            self._status_label.setText("Select a decoder.")
            return

        if not self._matches:
            self._scan_pairs()
            if not self._matches:
                return

        self.submit_settings(
            {
                "videos_dir": str(Path(self._videos_input.text().strip()).resolve()),
                "overlays_dir": str(Path(self._overlays_input.text().strip()).resolve()),
                "handler_type": handler_type,
            }
        )
