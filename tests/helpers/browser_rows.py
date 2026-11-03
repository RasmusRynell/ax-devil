"""Stores and projections the browser row tests share."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from types import MappingProxyType

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.workspace.core import (
    Content,
    OnScreenWorkspaceItem,
    PlaylistContent,
    PlaylistSettings,
    WorkspaceItem,
)
from ax_devil.modules.workspace.ui.browser_projection import build_browser_sections
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamStatus
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist
from tests.helpers.workspace import FakeResolutionContext, FakeResolver, content_item, inline_resolver


def browser_sections(
    store: WorkspaceStore,
    open_items: Set[OnScreenWorkspaceItem] = frozenset(),
    stream_statuses: Mapping[str, Sequence[LiveStreamStatus]] = MappingProxyType({}),
) -> tuple[WorkspaceBrowserSection, ...]:
    """Project the store's items the way the workspace controller does."""
    return build_browser_sections(
        store.resolutions(), store.is_item_considered, open_items, stream_statuses=stream_statuses
    )


def item_rows(
    store: WorkspaceStore,
    open_items: Set[OnScreenWorkspaceItem] = frozenset(),
    stream_statuses: Mapping[str, Sequence[LiveStreamStatus]] = MappingProxyType({}),
) -> tuple[WorkspaceBrowserRow, ...]:
    """Return the item rows under the sections, in section order."""
    return tuple(row for section in browser_sections(store, open_items, stream_statuses) for row in section.rows)


def store_of(*contents: Content) -> WorkspaceStore:
    """Return a store with one item per content."""
    store = WorkspaceStore(inline_resolver(FakeResolutionContext()))
    store.add_items([content_item(content) for content in contents])
    return store


class FixedPlaylists(FakeResolver):
    """A playlist resolver that returns the playlists it was built with."""

    def __init__(self, *playlists: PlaylistContent) -> None:
        self._playlists = list(playlists)

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return self._playlists


def store_of_items(*items: WorkspaceItem) -> WorkspaceStore:
    """Return a store resolving real *items*, with a playlist resolver named "fixed" that yields one playlist."""
    context = FakeResolutionContext(resolvers={"fixed": FixedPlaylists(make_playlist("Suite"))})
    store = WorkspaceStore(inline_resolver(context))
    store.add_items(items)
    return store
