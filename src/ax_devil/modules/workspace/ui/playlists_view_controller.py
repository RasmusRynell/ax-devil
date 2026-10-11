"""Feed the Playlists view from the workspace rows and the loaded resolver plugins, and act on its requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from PySide6.QtCore import QObject

from ax_devil.modules.plugin_system import PLAYLIST_RESOLVER_PLUGIN_TYPE, PluginStatus, RuntimePluginRegistry
from ax_devil.modules.workspace.core import PlaylistContent, PlaylistItem
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.playlists_view import (
    EntryListing,
    PlaylistListing,
    PlaylistState,
    PlaylistsView,
    ResolverListing,
)
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore


def _counted(count: int, noun: str, plural: str) -> str:
    return f"{count} {noun if count == 1 else plural}"


def _loaded_resolvers() -> tuple[ResolverListing, ...]:
    """Return the playlist resolver plugins that loaded; one from a plugin package names it first."""
    listings = []
    for record in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE):
        if record.status is not PluginStatus.LOADED:
            continue
        definition = record.definition
        origin = f"From {record.package}. " if record.package else ""
        listings.append(
            ResolverListing(definition.plugin_id, definition.display_name, f"{origin}{definition.description or ''}")
        )
    return tuple(listings)


def _entry(row: WorkspaceBrowserRow) -> EntryListing:
    assert row.activation_target is not None  # Every entry row opens its entry.
    playlist, index = row.activation_target
    lanes = len(cast(PlaylistContent, playlist).entries[index].lanes)
    return EntryListing(row.label, _counted(lanes, "lane", "lanes") if lanes > 1 else "", playlist, index)


def _playlist(row: WorkspaceBrowserRow, resolver_names: Mapping[str, str]) -> PlaylistListing:
    """Return the listing of a playlist row, which a pending or failed item has instead of its playlists."""
    item = cast(PlaylistItem, row.item)  # The Playlists section holds Playlist Items only.
    if row.unavailable_reason is not None:
        return PlaylistListing(row.row_id, item.id, row.label, row.unavailable_reason, PlaylistState.FAILED)
    if row.activation_target is None:
        return PlaylistListing(row.row_id, item.id, row.label, "Loading…", PlaylistState.PENDING)
    resolver = resolver_names.get(item.resolver, item.resolver)
    entries = _counted(len(row.children), "entry", "entries")
    return PlaylistListing(
        row.row_id,
        item.id,
        row.label,
        f"{resolver} · {entries}",
        PlaylistState.READY,
        tuple(_entry(child) for child in row.children),
    )


class PlaylistsViewController(QObject):
    """Show the workspace's playlists and the loaded resolvers in *view*, and carry out its requests.

    Playlists follow ``WorkspaceController.sections_changed``; resolvers are read again whenever the view comes on
    screen, so plugins reloaded meanwhile are listed. Rescans go to *store*, entries open through *workspace* like a
    double click in the browser, and adding stays with whoever handles ``view.add_requested``.
    """

    def __init__(
        self,
        view: PlaylistsView,
        workspace: WorkspaceController,
        store: WorkspaceStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view = view
        self._sections = workspace.sections()
        self._resolvers = _loaded_resolvers()
        view.rescan_requested.connect(store.rescan_item)
        view.entry_requested.connect(workspace.open_content)
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
        names = {resolver.resolver_id: resolver.title for resolver in self._resolvers}
        rows = [row for section in self._sections if section.kind is BrowserSection.PLAYLISTS for row in section.rows]
        self._view.set_listings([_playlist(row, names) for row in rows], self._resolvers)
