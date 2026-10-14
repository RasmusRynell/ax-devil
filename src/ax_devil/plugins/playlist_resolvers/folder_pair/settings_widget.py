"""Settings widget for the folder-pair playlist resolver."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import NamedTuple

from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import (
    JobContext,
    LatestRequest,
    PlaylistResolverWidget,
    get_file_decoder_definitions,
)

from .resolver import FolderPairMatch, discover_folder_pairs


class _Scanned(NamedTuple):
    """The resolved folders of a scan and the pairs found in them."""

    videos_dir: Path
    overlays_dir: Path
    matches: list[FolderPairMatch]


def _scan_folders(videos_dir: Path, overlays_dir: Path, job: JobContext[None]) -> _Scanned:
    matches = discover_folder_pairs(videos_dir, overlays_dir, job.token)
    return _Scanned(videos_dir.resolve(), overlays_dir.resolve(), matches)


class FolderPairSettingsWidget(PlaylistResolverWidget):
    """Edit and submit the video folder, overlay folder, and decoder of a folder-pair playlist."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scanned: _Scanned | None = None  # Set only when the scan found pairs.
        self._scan_came_up_empty = False  # The scan of the current folders found no pairs or failed.
        self._scan = LatestRequest(self)
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
        self._decoder_combo.addItem("Select a decoder", None)
        for decoder in get_file_decoder_definitions():
            self._decoder_combo.addItem(f"{decoder.display_name} ({decoder.handler_type})", decoder.handler_type)
        form.addRow("Decoder", self._decoder_combo)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._scan_pairs)
        action_row.addWidget(self._scan_btn)
        self._load_btn = QPushButton("Load Playlist")
        self._load_btn.clicked.connect(self._on_load)
        action_row.addWidget(self._load_btn)
        layout.addLayout(action_row)
        self._decoder_combo.currentIndexChanged.connect(self._refresh_load)
        self._refresh_load()

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
        self._start_scan(None)

    def _start_scan(self, load_handler: str | None) -> None:
        """Scan the folders; with a *load_handler*, submit the playlist using it as soon as pairs are found."""
        self._clear_matches()
        videos_text = self._videos_input.text().strip()
        overlays_text = self._overlays_input.text().strip()
        if not videos_text or not overlays_text:
            self._status_label.setText("Select video and overlay folders.")
            return

        self._status_label.setText("Scanning...")
        self._scan.submit(
            self.jobs,
            "scan folder pairs",
            partial(_scan_folders, Path(videos_text), Path(overlays_text)),
            on_result=partial(self._show_matches, load_handler),
            on_error=self._scan_failed,
        )
        self._refresh_load()

    def _scan_failed(self, error: Exception) -> None:
        self._scan_came_up_empty = True
        self._status_label.setText(f"Scan failed: {error}")
        self._refresh_load()

    def _show_matches(self, load_handler: str | None, scanned: _Scanned) -> None:
        if not scanned.matches:
            self._scan_came_up_empty = True
            self._status_label.setText("No matched video/overlay pairs found.")
            self._refresh_load()
            return

        self._scanned = scanned
        self._status_label.setText(f"Found {len(scanned.matches)} matched pair(s).")
        self._refresh_load()
        # A decoder changed since the click is a new choice; the user confirms it by loading again.
        if load_handler is not None and load_handler == self._decoder_combo.currentData():
            self._submit(scanned, load_handler)

    def _clear_matches(self) -> None:
        """Forget the scan, and stop the one in flight, whose folders are no longer the ones asked for."""
        self._scan.cancel()
        self._scanned = None
        self._scan_came_up_empty = False
        self._status_label.setText("")
        self._refresh_load()

    def _refresh_load(self) -> None:
        """Allow loading once both folders and a decoder are given, unless a scan runs or found nothing."""
        complete = (
            bool(self._videos_input.text().strip())
            and bool(self._overlays_input.text().strip())
            and isinstance(self._decoder_combo.currentData(), str)
        )
        self._load_btn.setEnabled(complete and not self._scan.pending and not self._scan_came_up_empty)

    def _on_load(self) -> None:
        handler_type = self._decoder_combo.currentData()
        if not isinstance(handler_type, str):
            return

        if self._scanned is None:
            self._start_scan(handler_type)
            return
        self._submit(self._scanned, handler_type)

    def _submit(self, scanned: _Scanned, handler_type: str) -> None:
        self.submit_settings(
            {
                "videos_dir": str(scanned.videos_dir),
                "overlays_dir": str(scanned.overlays_dir),
                "handler_type": handler_type,
            }
        )
