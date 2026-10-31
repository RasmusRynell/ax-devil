"""The Workspace store resolves items in the background and drops results that no longer apply."""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.workspace.core import ItemResolution, PlaylistContent, PlaylistItem, PlaylistSettings, Workspace
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist
from tests.helpers.workspace import FakeFileWatcher, FakeResolutionContext, FakeResolver
from tests.helpers.workspace_store import BlockingResolver, CountingResolver, TwoPlaylists, wait_for_jobs_since


def _background_store(resolver: BlockingResolver) -> WorkspaceStore:
    return WorkspaceStore(ItemResolver(FakeResolutionContext(resolvers={"runs": resolver, "quick": TwoPlaylists()})))


def test_items_resolve_away_from_the_gui_thread_and_are_pending_until_then(
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    store = _background_store(blocking)
    runs = PlaylistItem(label="Runs", resolver="runs")
    resolved: list[bool] = []
    store.items_resolved.connect(lambda _resolutions, added: resolved.append(added))

    store.add_items([runs])

    assert store.workspace.items == (runs,)
    assert store.is_modified
    assert store.resolution(runs.id).is_pending
    assert store.contents() == ()
    blocking.release.set()
    qtbot.waitUntil(lambda: resolved == [True])
    assert blocking.threads != [threading.main_thread()]
    assert [content.display_name for content in store.contents()] == ["Runs / train", "Runs / test"]


def test_a_result_follows_a_rename_made_while_the_item_resolved(qtbot: QtBot, blocking: BlockingResolver) -> None:
    store = _background_store(blocking)
    runs = PlaylistItem(label="Runs", resolver="runs")
    store.add_items([runs])

    store.rename_item(runs.id, "Exp 3")
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
    assert [content.display_name for content in store.contents()] == ["Exp 3 / train", "Exp 3 / test"]


def test_results_for_removed_items_or_a_replaced_workspace_are_dropped(
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    store = _background_store(blocking)
    removed, replaced = PlaylistItem(resolver="runs"), PlaylistItem(resolver="runs")
    kept = PlaylistItem(label="Kept", resolver="quick")
    store.add_items([removed])
    store.add_items([replaced])
    resolved: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda resolutions, _added: resolved.append(resolutions))

    store.remove_item(removed.id)
    store.replace_workspace(Workspace(items=(kept,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: len(resolved) == 1)
    assert [resolution.item for resolution in resolved[0]] == [kept]
    assert store.workspace.items == (kept,)
    assert [content.display_name for content in store.contents()] == ["Kept / train", "Kept / test"]


def test_a_replacing_workspace_resolves_without_waiting_for_the_one_it_replaced(
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    store = _background_store(blocking)
    store.replace_workspace(Workspace(items=(PlaylistItem(resolver="runs"),)))
    requested = PlaylistItem(label="Requested", resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending, timeout=2000)
    assert not blocking.release.is_set()
    assert [content.display_name for content in store.contents()] == ["Requested / train", "Requested / test"]


def test_items_of_an_opened_workspace_do_not_wait_for_a_slow_one(qtbot: QtBot, blocking: BlockingResolver) -> None:
    store = _background_store(blocking)
    slow, quick = PlaylistItem(resolver="runs"), PlaylistItem(label="Quick", resolver="quick")

    store.replace_workspace(Workspace(items=(slow, quick)))

    qtbot.waitUntil(lambda: not store.resolution(quick.id).is_pending, timeout=2000)
    assert store.resolution(slow.id).is_pending


def test_queued_items_of_a_replaced_workspace_are_never_resolved(qtbot: QtBot, blocking: BlockingResolver) -> None:
    counting = CountingResolver()
    resolver = FakeResolutionContext(resolvers={"runs": blocking, "counted": counting, "quick": TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(resolver))
    busy = tuple(PlaylistItem(resolver="runs") for _ in range(8))  # More than the resolver has workers.
    store.replace_workspace(Workspace(items=(*busy, PlaylistItem(resolver="counted"))))
    requested = PlaylistItem(resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending)
    assert counting.calls == 0


def test_replacing_a_workspace_stops_a_running_added_batch_before_its_next_item(
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    counting = CountingResolver()
    jobs = BackgroundJobs(max_workers=1)
    context = FakeResolutionContext(resolvers={"runs": blocking, "counted": counting, "quick": TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(context, jobs=jobs))
    store.add_items([PlaylistItem(resolver="runs"), PlaylistItem(resolver="counted")])
    qtbot.waitUntil(lambda: bool(blocking.threads))
    requested = PlaylistItem(label="Requested", resolver="quick")

    store.replace_workspace(Workspace(items=(requested,)))
    blocking.release.set()

    qtbot.waitUntil(lambda: not store.resolution(requested.id).is_pending)
    assert counting.calls == 0
    assert [content.display_name for content in store.contents()] == ["Requested / train", "Requested / test"]


def _jobs_with_a_busy_worker() -> tuple[BackgroundJobs, threading.Event, QObject]:
    """Return a one-worker pool whose only worker is held until the returned event is set."""
    jobs, busy, owner = BackgroundJobs(max_workers=1), threading.Event(), QObject()
    jobs.scope(owner).submit("busy", lambda _job: busy.wait(5), on_result=lambda _: None, on_error=lambda _: None)
    return jobs, busy, owner


@pytest.mark.parametrize("batched", [False, True], ids=["added-separately", "added-in-one-batch"])
def test_removing_an_item_skips_only_its_queued_resolution(qtbot: QtBot, batched: bool) -> None:
    resolver = CountingResolver()
    baseline = set(threading.enumerate())
    jobs, busy, _owner = _jobs_with_a_busy_worker()
    store = WorkspaceStore(ItemResolver(FakeResolutionContext(resolvers={"runs": resolver}), jobs=jobs))
    removed, kept = PlaylistItem(resolver="runs"), PlaylistItem(resolver="runs")
    if batched:
        store.add_items([removed, kept])
    else:
        store.add_items([removed])
        store.add_items([kept])

    store.remove_item(removed.id)
    busy.set()

    wait_for_jobs_since(qtbot, baseline)
    assert resolver.calls == 1 and not store.resolution(kept.id).is_pending


class _CooperativeResolver(FakeResolver):
    """Hold one call until its owning workspace operation cancels it."""

    def __init__(self, block_call: int = 1) -> None:
        self.calls = 0
        self.block_call = block_call
        self.started = threading.Event()
        self.stopped = threading.Event()

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return a numbered playlist, blocking the requested call with cooperative cancellation."""
        self.calls += 1
        if self.calls == self.block_call:
            self.started.set()
            try:
                token.wait(5)
            finally:
                self.stopped.set()
        return [make_playlist(f"scan {self.calls}")]


def test_removing_an_active_item_frees_the_worker_for_the_rest_of_its_added_batch(qtbot: QtBot) -> None:
    """Removing one slow playlist must stop its scan even while the batch still contains another item."""
    blocked = _CooperativeResolver()
    context = FakeResolutionContext(resolvers={"blocked": blocked, "quick": TwoPlaylists()})
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=1)))
    removed, kept = PlaylistItem(resolver="blocked"), PlaylistItem(resolver="quick")
    deliveries: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: deliveries.append(results))
    try:
        store.add_items([removed, kept])
        qtbot.waitUntil(blocked.started.is_set)

        store.remove_item(removed.id)

        qtbot.waitUntil(lambda: not store.resolution(kept.id).is_pending, timeout=2000)
        assert blocked.stopped.is_set()
        assert [[result.item for result in results] for results in deliveries] == [[kept]]
        assert store.workspace.items == (kept,)
    finally:
        store.replace_workspace(Workspace())
        assert blocked.stopped.wait(5)


def test_added_items_publish_once_in_input_order_when_later_items_finish_first(
    qtbot: QtBot, blocking: BlockingResolver
) -> None:
    counted = CountingResolver()
    context = FakeResolutionContext(resolvers={"slow": blocking, "quick": counted})
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=2)))
    slow, quick = PlaylistItem(resolver="slow"), PlaylistItem(resolver="quick")
    deliveries: list[list[ItemResolution]] = []
    store.items_resolved.connect(lambda results, _added: deliveries.append(results))
    store.add_items([slow, quick])
    qtbot.waitUntil(lambda: counted.calls == 1)
    assert deliveries == []

    blocking.release.set()

    qtbot.waitUntil(lambda: bool(deliveries))
    assert [[result.item for result in results] for results in deliveries] == [[slow, quick]]


def test_superseding_an_active_resolution_interrupts_it_before_the_next_one(qtbot: QtBot) -> None:
    """One workspace worker is enough for a replacement because its predecessor polls the job token."""
    resolver = _CooperativeResolver(block_call=2)
    context = FakeResolutionContext(resolvers={"runs": resolver})
    watcher = FakeFileWatcher()
    store = WorkspaceStore(ItemResolver(context, jobs=BackgroundJobs(max_workers=1)), watcher=watcher)
    runs = PlaylistItem(resolver="runs")
    delivered: list[list[ItemResolution]] = []
    try:
        store.add_items([runs])
        qtbot.waitUntil(lambda: not store.resolution(runs.id).is_pending)
        store.items_resolved.connect(lambda results, _added: delivered.append(results))
        watcher.report(runs.id)
        qtbot.waitUntil(resolver.started.is_set)

        watcher.report(runs.id)

        qtbot.waitUntil(lambda: bool(delivered), timeout=2000)
        assert resolver.stopped.is_set()
        assert [content.display_name for content in store.contents()] == ["scan 3"]
        assert len(delivered) == 1
    finally:
        store.replace_workspace(Workspace())
        assert resolver.stopped.wait(5)
