from __future__ import annotations

from threading import Event

import pytest
from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from ax_devil.modules.workspace.ui.add_content.analytics_discovery import AnalyticsChoiceLoader


@pytest.mark.parametrize("fail", [False, True])
def test_discovery_discards_stale_results_and_can_reload(qtbot: QtBot, fail: bool) -> None:
    """A changed connection invalidates both successful and failed in-flight requests."""
    release = Event()

    def fetch(host: str, username: str, password: str, protocol: str) -> tuple[str, ...]:
        if host == "old-camera":
            assert release.wait(5)
            if fail:
                raise ValueError("old connection failed")
        return (host,)

    loader = AnalyticsChoiceLoader(fetch, "topics")
    try:
        loader.set_connection(("old-camera", "", "", "https"))
        loader.load()
        loader.set_connection(("new-camera", "", "", "https"))
        release.set()
        qtbot.waitUntil(lambda: not loader.loading)
        assert not loader.loaded
        assert loader.choices == ()
        assert loader.status == "Connection changed — refresh topics"
        loader.load()
        qtbot.waitUntil(lambda: not loader.loading)
        assert loader.loaded
        assert loader.choices == ("new-camera",)
    finally:
        release.set()
        loader.cleanup()
        QThreadPool.globalInstance().waitForDone(6000)


def test_discovery_instances_load_independently_and_cleanup_ignores_results(qtbot: QtBot) -> None:
    """Closing one pending loader does not affect another transport's discovery."""
    release = Event()

    def fetch(host: str, username: str, password: str, protocol: str) -> tuple[str, ...]:
        assert release.wait(5)
        return (host,)

    loaders = [AnalyticsChoiceLoader(fetch, label) for label in ("data sources", "topics")]
    try:
        for loader in loaders:
            loader.set_connection(("camera", "", "", "https"))
            loader.load()
        loaders[0].cleanup()
        release.set()
        qtbot.waitUntil(lambda: loaders[1].loaded)
        assert not loaders[0].loaded
        assert loaders[0].choices == ()
        assert loaders[1].choices == ("camera",)
    finally:
        release.set()
        for loader in loaders:
            loader.cleanup()
        QThreadPool.globalInstance().waitForDone(6000)


def test_choice_refresh_preserves_user_selection(qtbot: QtBot) -> None:
    """Refreshing discovered choices preserves an available user selection."""
    loader = AnalyticsChoiceLoader(lambda *args: ("default", "selected"), "topics")
    choice = AnalyticsChoice(loader, "default")
    qtbot.addWidget(choice)
    try:
        loader.set_connection(("camera", "", "", "https"))
        loader.load()
        qtbot.waitUntil(lambda: not loader.loading)
        assert choice.combo.currentData() == "default"
        choice.combo.setCurrentIndex(choice.combo.findData("selected"))
        choice.refresh.click()
        qtbot.waitUntil(lambda: not loader.loading)
        assert choice.combo.currentData() == "selected"
    finally:
        choice.cleanup()
