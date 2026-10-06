"""Settings widget for the MOT Challenge playlist resolver.

Provides a directory browser that auto-discovers MOT sequences, displays them
in a checkable list, and emits playlist contents.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import PlaylistResolverWidget
from ax_devil.modules.settings.logging_config import get_logger

from .resolver import SequenceInfo, build_playlist_contents, discover_sequences

logger = get_logger(__name__)


class MOTChallengeSettingsWidget(PlaylistResolverWidget):
    """Settings widget for the MOT Challenge resolver.

    Emits ``playlist_resolved(list[PlaylistContent])`` when the user clicks "Load Playlist".
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequences: list[SequenceInfo] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.L)

        # Root directory row
        form = FormLayout()
        directory = QWidget()
        dir_row = QHBoxLayout(directory)
        dir_row.setContentsMargins(0, 0, 0, 0)
        self._root_input = QLineEdit()
        self._root_input.setPlaceholderText("Path to MOT dataset root directory")
        dir_row.addWidget(self._root_input, 1)
        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.clicked.connect(self._browse_root)
        dir_row.addWidget(self._browse_btn)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._scan_sequences)
        dir_row.addWidget(self._scan_btn)
        form.addRow("MOT Root:", directory)
        layout.addLayout(form)

        # Sequence list
        self._seq_list = QListWidget()
        layout.addWidget(self._seq_list, 1)

        # Status label
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # Load button
        self._load_btn = QPushButton("Load Playlist")
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load)
        layout.addWidget(self._load_btn)

    def _browse_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select MOT Dataset Root")
        if path:
            self._root_input.setText(path)
            self._scan_sequences()

    def _scan_sequences(self) -> None:
        root_text = self._root_input.text().strip()
        if not root_text:
            self._status_label.setText("Please enter a root directory path.")
            return

        root = Path(root_text)
        if not root.is_dir():
            self._status_label.setText("Directory does not exist.")
            return

        try:
            self._sequences = discover_sequences(root)
        except Exception as exc:
            logger.warning(f"Failed to scan MOT directory: {exc}")
            self._status_label.setText(f"Scan failed: {exc}")
            return

        self._seq_list.clear()

        if not self._sequences:
            self._status_label.setText("No MOT sequences found.")
            self._load_btn.setEnabled(False)
            return

        for seq in self._sequences:
            label = f"{seq.name}  —  {seq.width}x{seq.height} @ {seq.fps} fps, {seq.frame_count} frames"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._seq_list.addItem(item)

        self._status_label.setText(f"Found {len(self._sequences)} sequence(s).")
        self._load_btn.setEnabled(True)

    def _on_load(self) -> None:
        selected: list[SequenceInfo] = []
        for i in range(self._seq_list.count()):
            item = self._seq_list.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(self._sequences[i])

        if not selected:
            self._status_label.setText("No sequences selected.")
            return

        self.emit_playlists(build_playlist_contents(selected))
