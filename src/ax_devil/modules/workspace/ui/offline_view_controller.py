"""Feed the Offline view the workspace's videos and playlists, and the loaded resolver plugins that add playlists."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.workspace.ui.browser_rows import (
    BY_NAME,
    WORKSPACE_ORDER,
    BrowserSection,
    RowOrder,
)
from ax_devil.modules.workspace.ui.offline_view import OfflineView, PlaylistSource
from ax_devil.modules.workspace.ui.sections_view_controller import Sections, SectionsViewController
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController

BY_LOCATION = RowOrder("Location", lambda row: (row.location is None, (row.location or "").casefold()))
"""Rows by the folder or file they come from, the ones without a location last."""


def _loaded_resolvers() -> tuple[PluginRecord, ...]:
    """Return the playlist resolver plugins that loaded."""
    return tuple(
        record
        for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
        if record.status is PluginStatus.LOADED
    )


def _playlist_source(record: PluginRecord) -> PlaylistSource:
    """Return the source of a playlist resolver; its description and the plugin package it comes from show on hover."""
    definition = record.definition
    description = " ".join((definition.description or "").split())
    source = f"From {record.package}." if record.package else ""
    return PlaylistSource(
        definition.plugin_id, definition.display_name, "\n".join(line for line in (description, source) if line)
    )


class OfflineViewController(QObject):
    """Show the workspace's Videos and Playlists sections in *view*, and offer the resolvers to add playlists from.

    A resolver's row emits ``add_requested`` with its id, for whoever asks the user for the playlist's settings.
    """

    add_requested = Signal(str)  # resolver id

    def __init__(self, view: OfflineView, workspace: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._resolvers: tuple[PluginRecord, ...] | None = None
        SectionsViewController(
            view,
            workspace,
            {BrowserSection.VIDEOS, BrowserSection.PLAYLISTS},
            summary=_held_count,
            orders=(WORKSPACE_ORDER, BY_NAME, BY_LOCATION),
        )
        view.add_playlist_requested.connect(self.add_requested)
        view.shown.connect(self._refresh_resolvers)
        self._refresh_resolvers()

    def _refresh_resolvers(self) -> None:
        """Offer the loaded resolvers again when they changed."""
        resolvers = _loaded_resolvers()
        if resolvers != self._resolvers:
            self._resolvers = resolvers
            self._view.set_playlist_sources([_playlist_source(record) for record in resolvers])


def _held_count(sections: Sections) -> str:
    """Return how many rows of what the Workspace holds *sections* list, or nothing for none."""
    count = sum(len(section.rows) for section in sections if not section.kind.addable)
    return str(count) if count else ""
