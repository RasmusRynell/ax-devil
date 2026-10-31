"""Dialog for adding a playlist to the workspace via resolver plugins."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.theme import StatusColor, show_status
from ax_devil.modules.workspace.core import ItemResolution, PlaylistItem
from ax_devil.modules.workspace.ui.add_content.playlist_settings import PlaylistSettingsUI
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver


class AddPlaylistDialog(BaseDialog):
    """Dialog for adding a playlist via a resolver plugin.

    Embeds PlaylistSettingsUI and resolves the submitted Playlist Item with *resolver*, away from the GUI thread. It
    accepts with the result when the item resolves; otherwise the dialog stays open with the reason shown under the
    form. While the item resolves, further submissions are ignored. *resolver_id* chooses the playlist resolver the
    dialog opens with.
    """

    def __init__(
        self, resolver: ItemResolver, parent: QWidget | None = None, *, resolver_id: str | None = None
    ) -> None:
        self._result: ItemResolution | None = None
        self._resolver = resolver
        self._initial_resolver_id = resolver_id
        super().__init__(parent, title="Add Playlist")
        self._resolution = LatestRequest(self)
        self._setup_form()
        self.add_button("Cancel", self.reject)

    def _setup_form(self) -> None:
        self._settings = PlaylistSettingsUI(resolver_id=self._initial_resolver_id)
        self._settings.item_ready.connect(self._on_item_ready)
        self.add_content_widget(self._settings)
        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.add_content_widget(self._message_label)

    def _show_message(self, text: str, status: StatusColor | None = None) -> None:
        """Show *text* under the form, colored by *status* when given, growing the open dialog to fit it so the
        reason is never scrolled out of view."""
        self._message_label.setText(text)
        show_status(self._message_label, status)
        QTimer.singleShot(0, self, self._fit_to_message)

    def _fit_to_message(self) -> None:
        self._scroll_area.updateGeometry()  # The scroll area's hint changed, which its dialog's layout cannot see.
        self._refit_when_laid_out()

    def _on_item_ready(self, item: PlaylistItem) -> None:
        if self._resolution.pending:
            return
        self._show_message("Finding playlists…")
        self._resolution.start(
            lambda result, _error: self._resolver.resolve(item, result),
            on_result=self._on_resolved,
            on_error=lambda error: self._show_message(str(error), StatusColor.ERROR),
        )

    def _on_resolved(self, resolution: ItemResolution) -> None:
        """Accept with the resolved item, or show why it did not resolve."""
        if resolution.error is not None:
            self._show_message(str(resolution.error), StatusColor.ERROR)
            return
        self._result = resolution
        self.accept()

    def get_result(self) -> ItemResolution | None:
        """Return the resolved Playlist Item of the accepted dialog session, or None if it was cancelled."""
        return self._result

    def cleanup(self) -> None:
        """Release resolver forms and stop pending resolution after every dialog outcome."""
        self._resolution.close()
        self._settings.cleanup()
