"""Dialog for adding a playlist to the workspace via resolver plugins."""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.workspace.core import ItemResolutionError, PlaylistItem
from ax_devil.modules.workspace.ui.add_content.playlist_settings import PlaylistSettingsUI
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context


class AddPlaylistDialog(BaseDialog):
    """Dialog for adding a playlist via a resolver plugin.

    Embeds PlaylistSettingsUI and accepts as soon as the selected resolver submits settings that resolve; otherwise
    the dialog stays open with the reason shown under the form.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._result: PlaylistItem | None = None
        self._context = default_resolution_context()
        super().__init__(parent, title="Add Playlist")
        self._setup_form()
        self.add_button("Cancel", self.reject)

    def _setup_form(self) -> None:
        self._settings = PlaylistSettingsUI()
        self._settings.item_ready.connect(self._on_item_ready)
        self.add_content_widget(self._settings)
        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.add_content_widget(self._message_label)

    def _on_item_ready(self, item: PlaylistItem) -> None:
        try:
            item.resolve(self._context)
        except ItemResolutionError as exc:
            self._message_label.setText(str(exc))
            return
        self._result = item
        self.accept()

    def get_result(self) -> PlaylistItem | None:
        """Return the Playlist Item of the accepted dialog session, or None if it was cancelled."""
        return self._result

    def cleanup(self) -> None:
        """Release resolver forms after every dialog outcome."""
        self._settings.cleanup()
