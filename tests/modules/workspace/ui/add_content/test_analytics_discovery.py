from __future__ import annotations

from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.device_endpoint import DeviceEndpoint, DeviceProtocol
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from ax_devil.modules.workspace.ui.add_content.analytics_discovery import AnalyticsChoiceLoader, DeviceConnection


def _connection(host: str) -> DeviceConnection:
    return DeviceConnection(DeviceEndpoint(host, DeviceProtocol.HTTPS), "", "", 10)


@pytest.mark.parametrize("fail", [False, True])
def test_discovery_discards_stale_results_and_can_reload(qtbot: QtBot, fail: bool) -> None:
    """A changed connection invalidates both successful and failed in-flight requests."""
    release = Event()
    started = Event()
    finished = Event()

    def fetch(connection: DeviceConnection) -> tuple[str, ...]:
        host = connection.endpoint.host
        if host == "old-camera":
            started.set()
            try:
                assert release.wait(5)
                if fail:
                    raise ValueError("old connection failed")
            finally:
                finished.set()
        return (host,)

    loader = AnalyticsChoiceLoader(fetch, "topics")
    choice = AnalyticsChoice(loader, "")
    qtbot.addWidget(choice)
    try:
        loader.set_connection(_connection("old-camera"))
        loader.load()
        qtbot.waitUntil(started.is_set)
        loader.set_connection(_connection("new-camera"))
        assert not loader.loading
        assert choice.refresh.isEnabled()
        assert not loader.loaded
        assert loader.choices == ()
        assert loader.status == "Connection changed — refresh topics"
        choice.refresh.click()
        qtbot.waitUntil(lambda: not loader.loading)
        assert loader.loaded
        assert loader.choices == ("new-camera",)
        release.set()
        qtbot.waitUntil(finished.is_set)
    finally:
        release.set()
        loader.cleanup()


def test_discovery_instances_load_independently_and_cleanup_ignores_results(qtbot: QtBot) -> None:
    """Closing one pending loader does not affect another transport's discovery."""
    release = Event()

    def fetch(connection: DeviceConnection) -> tuple[str, ...]:
        assert release.wait(5)
        return (connection.endpoint.host,)

    loaders = [AnalyticsChoiceLoader(fetch, label) for label in ("data sources", "topics")]
    try:
        for loader in loaders:
            loader.set_connection(_connection("camera"))
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


def test_choice_refresh_preserves_user_selection(qtbot: QtBot) -> None:
    """Refreshing discovered choices preserves an available user selection."""
    loader = AnalyticsChoiceLoader(lambda _connection: ("default", "selected"), "topics")
    choice = AnalyticsChoice(loader, "default")
    qtbot.addWidget(choice)
    try:
        loader.set_connection(_connection("camera"))
        loader.load()
        qtbot.waitUntil(lambda: not loader.loading)
        assert choice.combo.currentData() == "default"
        choice.combo.setCurrentIndex(choice.combo.findData("selected"))
        choice.refresh.click()
        qtbot.waitUntil(lambda: not loader.loading)
        assert choice.combo.currentData() == "selected"
    finally:
        choice.cleanup()
