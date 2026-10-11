"""The Live view lists the workspace's cameras with connect and disconnect, and adds cameras found on the network."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.data_sources.live.network_scan import DiscoveredCamera
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem, Workspace, WorkspaceItem
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.live_view_controller import LiveViewController
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.workspace import DummyViewer

_W = TypeVar("_W", bound=QWidget)
_LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


class _HeldResolver(ItemResolver):
    """Runs work at once, or, while *hold* is set, keeps it until ``finish`` as work still under way would be."""

    def __init__(self) -> None:
        super().__init__(default_resolution_context(), in_background=False)
        self.hold = False
        self._held: list[tuple[Callable[[], Any], Callable[[Any], None], Callable[[], bool]]] = []

    def run(
        self, work: Callable[[], Any], done: Callable[[Any], None], wanted: Callable[[], bool] = lambda: True
    ) -> None:
        if self.hold:
            self._held.append((work, done, wanted))
        elif wanted():
            done(work())

    def finish(self) -> None:
        held, self._held = self._held, []
        for work, done, wanted in held:
            if wanted():
                done(work())


@dataclass
class _Scanner:
    """A network scan that finds *cameras*, or fails with *error*, and counts its calls."""

    cameras: list[DiscoveredCamera] = field(default_factory=list)
    error: Exception | None = None
    calls: int = 0

    def __call__(self) -> list[DiscoveredCamera]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.cameras


@dataclass
class _Live:
    view: LiveView
    store: WorkspaceStore
    center: SplitView
    scanner: _Scanner
    scans: _HeldResolver
    resolutions: _HeldResolver

    def open_workspace(self, *items: WorkspaceItem) -> None:
        """Make *items* the workspace, opening nothing, as restoring one does."""
        self.store.replace_workspace(Workspace(items=items))

    def rows(self) -> dict[str, str]:
        """Return each listed row's title and its action, empty for none, in order."""
        return {_title(row): _action(row) for row in _shown(self.view, ListRow)}

    def subtitles(self) -> list[str]:
        return [labels[1].full_text() for row in _shown(self.view, ListRow) if len(labels := _labels(row)) > 1]

    def press(self, title: str) -> None:
        """Press the action of the row titled *title*."""
        [row] = [row for row in _shown(self.view, ListRow) if _title(row) == title]
        [button] = _shown(row, QPushButton)
        button.click()

    def button(self, text: str) -> QPushButton:
        [button] = [button for button in _shown(self.view, QPushButton) if button.text() == text]
        return button

    def texts(self) -> list[str]:
        return [label.text() for label in _shown(self.view, QLabel) if label.text()]


def _shown(parent: QWidget, kind: type[_W]) -> list[_W]:
    """Return *parent*'s children of *kind* that were not removed, which hides them until they are deleted."""
    return [child for child in parent.findChildren(kind) if not child.isHidden()]


def _labels(row: ListRow) -> list[ElidedLabel]:
    return row.findChildren(ElidedLabel)


def _title(row: ListRow) -> str:
    return _labels(row)[0].full_text()


def _action(row: ListRow) -> str:
    return "".join(button.text() for button in _shown(row, QPushButton))


@pytest.fixture
def live(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> Generator[_Live, None, None]:
    resolutions, scans = _HeldResolver(), _HeldResolver()
    scans.hold = True
    store = WorkspaceStore(resolutions)
    center = SplitView()
    workspace = WorkspaceController(store, ContentBrowserWidget(), center, render_catalog_manager)
    host = QWidget()  # Holds the view, so a test can delete the view alone.
    qtbot.addWidget(host)
    view = LiveView(host)
    qtbot.addWidget(center)
    scanner = _Scanner()
    LiveViewController(view, workspace, store, scans, scan=scanner, parent=workspace)
    with patch(_LIVE_VIEWER, DummyViewer):
        yield _Live(view, store, center, scanner, scans, resolutions)


def _scan(live: _Live, *cameras: DiscoveredCamera) -> None:
    live.scanner.cameras = list(cameras)
    live.button("Scan network").click()
    live.scans.finish()


def test_rows_show_which_cameras_are_connected_and_follow_their_viewers(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    assert live.rows() == {"Entrance": "Connect", "192.0.2.11": "Connect"}
    assert not any(text.endswith("connected") for text in live.texts())

    live.press("Entrance")
    assert live.center.get_widget_count() == 1
    assert live.rows() == {"Entrance": "Disconnect", "192.0.2.11": "Connect"}
    assert "1 connected" in live.texts()

    live.press("192.0.2.11")
    assert live.center.get_widget_count() == 2, "connecting a camera never disconnects another"
    assert "2 connected" in live.texts()

    live.center.clear_all_widgets()
    assert live.rows() == {"Entrance": "Connect", "192.0.2.11": "Connect"}


def test_disconnect_closes_every_viewer_of_the_camera(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    live.press("Entrance")
    assert live.rows() == {"Entrance": "Disconnect"}

    live.press("Entrance")

    assert live.center.get_widget_count() == 0
    assert live.rows() == {"Entrance": "Connect"}


def test_cameras_that_cannot_open_yet_or_at_all_offer_no_action(live: _Live) -> None:
    broken = LiveStreamItem(host="192.0.2.10", label="Dock", overlay_mode=LiveOverlayMode.RTSP, handler_type="nope")
    live.open_workspace(broken)
    live.resolutions.hold = True
    live.store.add_items([LiveStreamItem(host="192.0.2.11", label="Gate")])

    assert live.rows() == {"Dock": "", "Gate": ""}
    assert any("nope" in subtitle for subtitle in live.subtitles())

    live.resolutions.finish()
    assert live.rows()["Gate"] == "Disconnect", "an added camera opens once resolved"


def test_nothing_scans_until_scan_network_is_pressed(qtbot: QtBot, live: _Live) -> None:
    live.view.window().show()
    qtbot.waitExposed(live.view)
    live.scans.finish()
    assert live.scanner.calls == 0
    assert "On the network" not in str(live.texts())

    _scan(live)

    assert live.scanner.calls == 1


def test_the_scan_button_waits_while_a_scan_runs(live: _Live) -> None:
    live.button("Scan network").click()
    assert not live.button("Scan network").isEnabled()
    assert "Scanning…" in live.texts()

    live.scans.finish()

    assert live.button("Scan network").isEnabled()
    assert "Scanning…" not in live.texts()


def test_a_scan_lists_only_cameras_the_workspace_does_not_hold(live: _Live, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_DEVIL_TEST_LIVE_CAMERA", "192.0.2.11")
    live.open_workspace(
        LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="$AX_DEVIL_TEST_LIVE_CAMERA")
    )

    _scan(
        live,
        DiscoveredCamera("192.0.2.10", "AXIS Q1656", "ACCC8E000001"),
        DiscoveredCamera("192.0.2.11", "AXIS M3086-V", ""),
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002"),
    )

    assert list(live.rows())[2:] == ["AXIS P3265-LVE"]
    assert live.rows()["AXIS P3265-LVE"] == "Add"
    assert "192.0.2.12 · ACCC8E000002" in live.subtitles()

    _scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", ""))

    assert list(live.rows())[2:] == []
    assert "No new cameras" in live.texts()


def test_add_puts_the_camera_in_the_workspace_with_the_stream_defaults(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_instance", None)
    config = ConfigManager()
    config.set_config_path(tmp_path / "config.json")
    config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
    defaults = config.get_raw("defaults")
    defaults["device"] = {"host": "$AX_DEVIL_TARGET_ADDR", "username": "$CAMERA_USER", "password": "$CAMERA_PASS"}
    defaults["live_stream"]["rtsp"].update(
        {"camera_head": 2, "resolution": "640x480", "data_stream_handler": "ONVIF_XML"}
    )
    defaults["live_stream"]["overlay_source"] = "rtsp"
    config.set("defaults", defaults)
    _scan(live, DiscoveredCamera("192.0.2.34", "AXIS P3265-LVE", ""))

    live.press("AXIS P3265-LVE")

    [item] = live.store.workspace.items
    assert isinstance(item, LiveStreamItem)
    assert (item.host, item.label, item.username, item.password) == (
        "192.0.2.34",
        "AXIS P3265-LVE",
        "$CAMERA_USER",
        "$CAMERA_PASS",
    )
    assert (item.camera_head, item.resolution, item.overlay_mode) == (2, "640x480", LiveOverlayMode.RTSP)
    assert live.rows() == {"AXIS P3265-LVE": "Disconnect"}
    assert "No new cameras" in live.texts()


def test_a_failed_scan_says_so_and_keeps_the_reason_on_hover(live: _Live) -> None:
    live.scanner.error = OSError("Network is unreachable")
    live.button("Scan network").click()
    live.scans.finish()

    [failed] = [label for label in _shown(live.view, QLabel) if label.text() == "Scan failed"]
    assert "Network is unreachable" in failed.toolTip()
    assert live.button("Scan network").isEnabled()


def test_a_scan_is_dropped_once_the_view_is_gone(live: _Live) -> None:
    live.button("Scan network").click()

    live.view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    live.scans.finish()

    assert live.scanner.calls == 0
