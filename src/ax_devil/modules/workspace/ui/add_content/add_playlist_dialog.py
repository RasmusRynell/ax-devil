"""Dialog for adding a playlist to the workspace via resolver plugins."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.content import PlaylistContent
from ax_devil.modules.workspace.ui.add_content.playlist_settings import PlaylistSettingsUI

logger = get_logger(__name__)


class AddPlaylistDialog(BaseDialog):
    """Dialog for adding a playlist via a resolver plugin.

    Embeds PlaylistSettingsUI and accepts as soon as the selected resolver
    produces playlist contents.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        self._result: list[PlaylistContent] = []
        super().__init__(parent, title="Add Playlist")
        self._setup_form()
        self.add_button("Cancel", self.reject)

    def _setup_form(self) -> None:
        self._settings = PlaylistSettingsUI()
        self._settings.playlist_resolved.connect(self._on_playlist_resolved)
        self.add_content_widget(self._settings)

    def _on_playlist_resolved(self, playlist_contents: list[PlaylistContent]) -> None:
        """Handle resolved playlist contents from the settings UI."""
        self._result = list(playlist_contents)
        logger.info(f"Resolved {len(self._result)} playlists from resolver")
        self.accept()

    def get_result(self) -> list[PlaylistContent]:
        """Return the playlists produced during the accepted dialog session."""
        return list(self._result)

    def cleanup(self) -> None:
        """Release resolver forms after every dialog outcome."""
        self._settings.cleanup()
