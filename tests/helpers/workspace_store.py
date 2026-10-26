"""Resolvers and stores the Workspace store tests share."""

from __future__ import annotations

import threading
from typing import cast

from PySide6.QtCore import QCoreApplication
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.modules.workspace.core import PlaylistContent, PlaylistSettings
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.contents import make_playlist
from tests.helpers.workspace import FakeResolutionContext, FakeResolver, inline_resolver


class TwoPlaylists(FakeResolver):
    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        return [make_playlist("train"), make_playlist("test")]


def playlist_at(store: WorkspaceStore, index: int = 0) -> PlaylistContent:
    return cast(PlaylistContent, store.contents()[index])


def two_playlist_store() -> WorkspaceStore:
    return WorkspaceStore(inline_resolver(FakeResolutionContext(resolvers={"runs": TwoPlaylists()})))


class CountingResolver(FakeResolver):
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        self.calls += 1
        return [make_playlist("train"), make_playlist("test")]


class BlockingResolver(FakeResolver):
    """Resolve to two playlists once the test releases it, recording the thread it ran on."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.threads: list[threading.Thread] = []

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        self.threads.append(threading.current_thread())
        assert self.release.wait(timeout=5)
        return [make_playlist("train"), make_playlist("test")]


def wait_for_jobs_since(qtbot: QtBot, baseline: set[threading.Thread]) -> None:
    """Wait until the workers started after *baseline* ran out of jobs, then let the GUI thread take every outcome."""
    qtbot.waitUntil(lambda: not set(threading.enumerate()) - baseline)
    QCoreApplication.processEvents()
