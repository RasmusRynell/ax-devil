"""Dialog for adding a playlist to the workspace via resolver plugins."""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.workspace.core import ItemResolution, PlaylistItem
from ax_devil.modules.workspace.ui.add_content.playlist_settings import PlaylistSettingsUI
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver


class AddPlaylistDialog(BaseDialog):
    """Dialog for adding a playlist via a resolver plugin.

    Embeds PlaylistSettingsUI and resolves the submitted Playlist Item with *resolver*, away from the GUI thread. It
    accepts with the result when the item resolves; otherwise the dialog stays open with the reason shown under the
    form. While the item resolves, further submissions are ignored.
    """

    def __init__(self, resolver: ItemResolver, parent: QWidget | None = None) -> None:
        self._result: ItemResolution | None = None
        self._resolver = resolver
        self._resolving = False
        self._closed = False
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
        if self._resolving:
            return
        self._resolving = True
        self._message_label.setText("Finding playlists…")
        self._resolver.resolve([item], self._on_resolved)

    def _on_resolved(self, resolutions: tuple[ItemResolution, ...]) -> None:
        """Accept with the resolved item, or show why it did not resolve; a closed dialog ignores the result."""
        if self._closed:
            return
        self._resolving = False
        resolution = resolutions[0]
        if resolution.error is not None:
            self._message_label.setText(str(resolution.error))
            return
        self._result = resolution
        self.accept()

    def get_result(self) -> ItemResolution | None:
        """Return the resolved Playlist Item of the accepted dialog session, or None if it was cancelled."""
        return self._result

    def cleanup(self) -> None:
        """Release resolver forms after every dialog outcome, and ignore a result still on its way."""
        self._closed = True
        self._settings.cleanup()
