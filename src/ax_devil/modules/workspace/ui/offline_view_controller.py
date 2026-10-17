"""Feed the Offline view the workspace's videos and playlists, and the loaded resolver plugins that add playlists."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.offline_view import OfflineView
from ax_devil.modules.workspace.ui.sections_view_controller import Sections, SectionsViewController
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController


def _loaded_resolvers() -> tuple[PluginRecord, ...]:
    """Return the playlist resolver plugins that loaded."""
    return tuple(
        record
        for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
        if record.status is PluginStatus.LOADED
    )


def _resolver_row(record: PluginRecord, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a playlist resolver, whose Add does *add*; its description and the plugin package it
    comes from show on hover."""
    definition = record.definition
    description = " ".join((definition.description or "").split())
    source = f"From {record.package}." if record.package else ""
    return WorkspaceBrowserRow(
        row_id=f"resolver/{definition.plugin_id}",
        label=definition.display_name,
        icon_kind="playlist",
        facts=tuple(line for line in ("Playlist resolver", description, source) if line),
        action=RowAction.ADD,
        add=add,
    )


class OfflineViewController(QObject):
    """Show the workspace's Videos and Playlists sections in *view*, then the loaded resolvers to add playlists from.

    A resolver's Add emits ``add_requested`` with its id, for whoever asks the user for the playlist's settings.
    """

    add_requested = Signal(str)  # resolver id

    def __init__(self, view: OfflineView, workspace: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._resolvers: tuple[PluginRecord, ...] = ()
        self._resolver_rows: tuple[WorkspaceBrowserRow, ...] = ()
        self._listing = SectionsViewController(
            view,
            workspace,
            {BrowserSection.VIDEOS, BrowserSection.PLAYLISTS},
            compose=self._with_resolvers,
            summary=_held_count,
        )
        view.shown.connect(self._refresh_resolvers)

    def _refresh_resolvers(self) -> None:
        if _loaded_resolvers() != self._resolvers:
            self._listing.refresh()

    def _with_resolvers(self, held: Sections) -> Sections:
        """Return *held*, then the loaded resolvers."""
        resolvers = _loaded_resolvers()
        if resolvers != self._resolvers:
            self._resolvers = resolvers
            self._resolver_rows = tuple(
                _resolver_row(record, partial(self.add_requested.emit, record.definition.plugin_id))
                for record in resolvers
            )
        if not self._resolver_rows:
            return held
        return (*held, WorkspaceBrowserSection(BrowserSection.ADD_FROM, self._resolver_rows))


def _held_count(sections: Sections) -> str:
    """Return how many rows of what the Workspace holds *sections* list, or nothing for none."""
    count = sum(len(section.rows) for section in sections if not section.kind.addable)
    return str(count) if count else ""
