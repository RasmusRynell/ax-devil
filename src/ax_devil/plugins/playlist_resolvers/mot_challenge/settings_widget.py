"""Settings widget for the MOT Challenge playlist resolver.

Provides a directory browser that auto-discovers MOT sequences, displays them
in a checkable list, and submits the root and the checked sequence names.
"""

from __future__ import annotations

from copy import deepcopy
from functools import partial
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

from ax_devil.modules.chrome.browse_button import BrowseButton
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import JobContext, LatestRequest, PlaylistResolverWidget
from ax_devil.modules.workspace.core import PlaylistSettings

from .resolver import SequenceInfo, discover_sequences


def _scan_root(root: Path, job: JobContext[None]) -> tuple[Path, list[SequenceInfo]]:
    """Return the resolved *root* and the sequences under it."""
    sequences = discover_sequences(root, job.token)
    return root.resolve(), sequences


class MOTChallengeSettingsWidget(PlaylistResolverWidget):
    """Settings widget for the MOT Challenge resolver.

    Submits ``{"root": ..., "sequences": [...]}`` when the user clicks "Load Playlist".
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._restored: PlaylistSettings = {}
        self._saved_names: set[str] | None = None
        self._all_sequences = False
        self._sequences: list[SequenceInfo] = []
        self._scanned_root: Path | None = None
        self._scan = LatestRequest(self)
        self._setup_ui()

    def restore_settings(self, settings: PlaylistSettings) -> bool:
        """Restore the root and selection after scanning, preserving the all-sequences recipe."""
        self._root_input.setText(str(settings.get("root", "")))
        self._restored = deepcopy(settings)
        self._all_sequences = "sequences" not in settings
        self._saved_names = None if self._all_sequences else set(settings["sequences"])
        self._load_btn.setText("Save")
        self._scan_sequences()
        return True

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
        self._root_input.textChanged.connect(self._clear_sequences)
        dir_row.addWidget(self._root_input, 1)
        self._browse_btn = BrowseButton("the MOT dataset folder", self._browse_root)
        dir_row.addWidget(self._browse_btn)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._scan_sequences)
        dir_row.addWidget(self._scan_btn)
        form.addRow("MOT Root", directory)
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

    def _clear_sequences(self, *, input_changed: bool = True) -> None:
        """Invalidate both pending and completed discovery when its input changes."""
        if input_changed:
            self._saved_names = None
            self._all_sequences = False
            self._restored = {key: value for key, value in self._restored.items() if key != "sequences"}
        self._scan.cancel()
        self._scanned_root = None
        self._sequences = []
        self._seq_list.clear()
        self._load_btn.setEnabled(False)
        self._status_label.setText("")

    def _scan_sequences(self) -> None:
        self._clear_sequences(input_changed=False)
        root_text = self._root_input.text().strip()
        if not root_text:
            self._status_label.setText("Please enter a root directory path.")
            return

        self._status_label.setText("Scanning...")
        self._scan.submit(
            self.jobs,
            "scan MOT sequences",
            partial(_scan_root, Path(root_text)),
            on_result=self._show_sequences,
            on_error=self._scan_failed,
        )

    def _scan_failed(self, error: Exception) -> None:
        self._status_label.setText(f"Scan failed: {error}")

    def _show_sequences(self, scanned: tuple[Path, list[SequenceInfo]]) -> None:
        self._scanned_root, self._sequences = scanned

        if not self._sequences:
            self._status_label.setText("No MOT sequences found.")
            self._load_btn.setEnabled(False)
            return

        for seq in self._sequences:
            label = f"{seq.name}  —  {seq.width}x{seq.height} @ {seq.fps} fps, {seq.frame_count} frames"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = self._saved_names is None or seq.name in self._saved_names
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._seq_list.addItem(item)

        missing = (self._saved_names or set()) - {seq.name for seq in self._sequences}
        detail = f" {len(missing)} saved sequences not found: {', '.join(sorted(missing))}." if missing else ""
        self._status_label.setText(f"Found {len(self._sequences)} sequence(s).{detail}")
        self._load_btn.setEnabled(True)

    def _on_load(self) -> None:
        if self._scan.pending or self._scanned_root is None:
            return
        selected: list[SequenceInfo] = []
        for i in range(self._seq_list.count()):
            item = self._seq_list.item(i)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                selected.append(self._sequences[i])

        if not selected:
            self._status_label.setText("No sequences selected.")
            return

        saved_root = self._restored.get("root")
        root = saved_root if self._root_input.text() == saved_root else str(self._scanned_root)
        settings = {**self._restored, "root": root}
        if not self._all_sequences or len(selected) != len(self._sequences):
            selected_names = [sequence.name for sequence in selected]
            discovered = {sequence.name for sequence in self._sequences}
            original = self._restored.get("sequences", [])
            kept = [name for name in original if name in selected_names or name not in discovered]
            settings["sequences"] = [*kept, *(name for name in selected_names if name not in kept)]
        else:
            settings.pop("sequences", None)
        self.submit_settings(settings)
