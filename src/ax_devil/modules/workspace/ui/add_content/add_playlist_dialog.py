"""Dialog for adding a playlist to the workspace via resolver plugins."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.form_layout import FormLayout
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
        self,
        resolver: ItemResolver,
        parent: QWidget | None = None,
        *,
        resolver_id: str | None = None,
        existing: PlaylistItem | None = None,
    ) -> None:
        self._existing = existing
        self._result: ItemResolution | None = None
        self._resolver = resolver
        self._initial_resolver_id = resolver_id
        super().__init__(parent, title="Edit Playlist" if existing is not None else "Add Playlist")
        self._resolution = LatestRequest(self)
        self._setup_form()
        self.add_button("Cancel", self.reject)

    def _setup_form(self) -> None:
        name_form = QWidget()
        layout = FormLayout(name_form)
        self._name_edit = QLineEdit(self._existing.label if self._existing is not None else "")
        self._name_edit.setPlaceholderText("Optional")
        layout.addRow("Display Name", self._name_edit)
        self.add_content_widget(name_form)
        self._settings = PlaylistSettingsUI(resolver_id=self._initial_resolver_id, existing=self._existing)
        self._settings.item_ready.connect(self._on_item_ready)
        self.add_content_widget(self._settings)
        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.add_content_widget(self._message_label)

    def _on_item_ready(self, item: PlaylistItem) -> None:
        if self._resolution.pending:
            return
        item = replace(item, label=self._name_edit.text().strip())
        self._message_label.setText("Finding playlists…")
        self._resolution.start(
            lambda result, _error: self._resolver.resolve(item, result),
            on_result=self._on_resolved,
            on_error=lambda error: self._message_label.setText(str(error)),
        )

    def _on_resolved(self, resolution: ItemResolution) -> None:
        """Accept with the resolved item, or show why it did not resolve."""
        if resolution.error is not None:
            self._message_label.setText(str(resolution.error))
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
