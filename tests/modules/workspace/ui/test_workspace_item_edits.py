"""Committed recipe edits preserve user data and reject obsolete resolution work."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.workspace.core import (
    BookmarkTarget,
    ItemResolution,
    PlaylistContent,
    PlaylistItem,
    PlaylistSettings,
    WorkspaceItem,
    load_workspace,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver, ResolutionReady
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist
from tests.helpers.workspace import FakeFileWatcher, FakeResolutionContext, FakeResolver, inline_resolver


class _Folder(FakeResolver):
    """Build one playlist from a recipe, and report the folder the recipe names."""

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [make_playlist(str(settings["root"]))]

    def source_paths(self, settings: PlaylistSettings, token: CancelToken) -> SourcePaths:
        return SourcePaths(folders=frozenset({Path(settings["root"])}))


class _ManualResolver(ItemResolver):
    """Deliver outcomes even after cancellation, including callbacks during cancellation."""

    def __init__(self) -> None:
        super().__init__(FakeResolutionContext(resolvers={"folder": _Folder()}), in_background=False)
        self.requests: list[Callable[[], None]] = []
        self.finish_on_cancel = False

    def resolve(self, item: WorkspaceItem, done: ResolutionReady) -> Callable[[], None]:
        """Retain a completion callback until the test delivers it."""

        def complete() -> None:
            done(ItemResolution.of(item, self.context, NEVER))

        self.requests.append(complete)

        def cancel() -> None:
            if self.finish_on_cancel:
                complete()

        return cancel


def test_edit_preserves_saved_review_data_but_replaces_watches_and_lane_exclusions(tmp_path: Path) -> None:
    watcher = FakeFileWatcher()
    store = WorkspaceStore(inline_resolver(FakeResolutionContext(resolvers={"folder": _Folder()})), watcher=watcher)
    item = PlaylistItem(resolver="folder", settings={"root": str(tmp_path / "before")})
    store.add_items([item], open_first=False)
    content = store.contents()[0]
    assert isinstance(content, PlaylistContent)
    target = BookmarkTarget(item_id=item.id, entry=content.bookmark_entries[0])
    bookmark = store.add_bookmark(target, 3, 0.1)
    store.set_reviewed(target, True)
    ref = content.entry_consideration_ref(0)
    store.set_item_considered(ref, False)
    store.save_workspace(tmp_path / "work.ax-devil.workspace")
    saved = store.workspace
    notifications: list[object] = []
    store.item_edited.connect(notifications.append)
    store.items_resolved.connect(lambda results, _open: notifications.extend(results))

    store.edit_item(item)
    assert store.workspace is saved and not store.is_modified and notifications == []
    with pytest.raises(ValueError):
        store.edit_item(replace(item, settings={"root": "/different"}), store.resolution(item.id))
    assert store.workspace is saved

    edited = replace(item, settings={"root": str(tmp_path / "after")})
    store.edit_item(edited)

    assert notifications[0] == edited and len(notifications) == 2
    assert watcher.watched[item.id].paths.folders == {tmp_path / "after"}
    changed_content = store.contents()[0]
    assert isinstance(changed_content, PlaylistContent)
    assert store.is_item_considered(changed_content.entry_consideration_ref(0))
    assert store.bookmarks == (bookmark,) and store.reviewed == {target}
    store.save_workspace()
    reopened = load_workspace(store.workspace.path or Path())
    assert reopened.items == (edited,) and reopened.bookmarks == (bookmark,) and reopened.reviewed == {target}

    resolution = store.resolution(item.id)
    store.edit_item(replace(edited, label="New label"))
    assert len(notifications) == 2, "renaming must not resolve or trigger media replacement"
    assert store.resolution(item.id).by_default_name is resolution.by_default_name


@pytest.mark.parametrize("watching", [False, True], ids=["initial-resolution", "file-watch-resolution"])
def test_edit_rejects_obsolete_completions_even_when_cancellation_delivers_them(watching: bool) -> None:
    resolver, watcher = _ManualResolver(), FakeFileWatcher()
    store = WorkspaceStore(resolver, watcher=watcher)
    item = PlaylistItem(resolver="folder", settings={"root": "/before"})
    store.add_items([item], open_first=False)
    if watching:
        resolver.requests[0]()
        watcher.report(item.id)
    old = resolver.requests[-1]
    resolver.finish_on_cancel = True
    edited = replace(item, settings={"root": "/after"})
    delivered: list[ItemResolution] = []
    store.items_resolved.connect(lambda results, _open: delivered.extend(results))

    store.edit_item(edited)
    old()
    assert store.resolution(item.id).is_pending and delivered == []
    resolver.requests[-1]()
    old()

    assert store.resolution(item.id).item == edited
    assert [content.display_name for content in store.contents()] == ["/after"]
    assert len(delivered) == 1
