"""Feed the Playlists view the workspace's playlists and the loaded resolver plugins that can add more."""

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
from ax_devil.modules.workspace.ui.playlists_view import PlaylistsView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController


def _loaded_resolvers() -> tuple[PluginRecord, ...]:
    """Return the playlist resolver plugins that loaded."""
    return tuple(
        record
        for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
        if record.status is PluginStatus.LOADED
    )


def _resolver_row(record: PluginRecord, add: Callable[[], None]) -> WorkspaceBrowserRow:
    """Return the row of a playlist resolver, whose Add does *add*: its description on one line, and in full on
    hover with the plugin package it comes from."""
    definition = record.definition
    description = " ".join((definition.description or "").split())
    origin = f"From {record.package}." if record.package else ""
    return WorkspaceBrowserRow(
        row_id=f"resolver/{definition.plugin_id}",
        label=definition.display_name,
        icon_kind="playlist",
        detail=description,
        facts=tuple(line for line in ("Playlist resolver", description, origin) if line),
        action=RowAction.ADD,
        add=add,
    )


class PlaylistsViewController(QObject):
    """Show the workspace's Playlists section and the loaded resolvers in *view*.

    Playlists follow ``WorkspaceController.sections_changed``, and *workspace* carries out what their rows ask for;
    resolvers are read again whenever the view comes on screen, so plugins reloaded meanwhile are listed. A resolver's
    Add emits ``add_requested`` with its id, for whoever asks the user for the playlist's settings.
    """

    add_requested = Signal(str)  # resolver id

    def __init__(self, view: PlaylistsView, workspace: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._sections = workspace.sections()
        self._resolvers = _loaded_resolvers()
        workspace.connect_tree(view.tree)
        view.shown.connect(self._refresh_resolvers)
        workspace.sections_changed.connect(self._show_sections)
        self._show()

    def _show_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        self._sections = sections
        self._show()

    def _refresh_resolvers(self) -> None:
        resolvers = _loaded_resolvers()
        if resolvers != self._resolvers:
            self._resolvers = resolvers
            self._show()

    def _show(self) -> None:
        """Show the Playlists section, counted in the header, and the resolvers to add more from."""
        shown = tuple(section for section in self._sections if section.kind is BrowserSection.PLAYLISTS)
        count = sum(len(section.rows) for section in shown)
        resolvers = tuple(
            _resolver_row(record, partial(self.add_requested.emit, record.definition.plugin_id))
            for record in self._resolvers
        )
        if resolvers:
            shown += (WorkspaceBrowserSection(BrowserSection.ADD_FROM, resolvers),)
        self._view.set_sections(shown, str(count) if count else "")
