"""The Live view lists the workspace's cameras with start and stop stream, and adds cameras found on the network."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login, SignIn
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.core import ItemResolution, LiveOverlayMode, LiveStreamItem, Workspace, WorkspaceItem
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.live_view_controller import LiveViewController
from ax_devil.modules.workspace.ui.logins_editor import LoginsEditor
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.section_tree import captions, press, row, row_action, row_texts, shown_rows, texts
from tests.helpers.widgets import button
from tests.helpers.workspace import DummyViewer

_LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


class _HeldResolver(ItemResolver):
    """Runs work at once, or, while *hold* is set, keeps it until ``finish`` as work still under way would be."""

    def __init__(self) -> None:
        super().__init__(default_resolution_context(), in_background=False)
        self.hold = False
        self._held: list[tuple[WorkspaceItem, Callable[[ItemResolution], None], CancelToken]] = []

    def resolve(self, item: WorkspaceItem, done: Callable[[ItemResolution], None]) -> Callable[[], None]:
        """Resolve immediately unless this test is holding item delivery."""
        if not self.hold:
            return super().resolve(item, done)
        token = CancelToken()
        self._held.append((item, done, token))
        return token.cancel

    def finish(self) -> None:
        """Finish held items unless their owner cancelled them."""
        held, self._held = self._held, []
        for item, done, token in held:
            try:
                done(ItemResolution.of(item, self.context, token))
            except Cancelled:
                pass


@dataclass
class _Scanner:
    """A network scan that reports *cameras* one by one, or fails with *error*, and records how it was called; it
    waits while *gate* is clear, as a scan still under way would.

    *on_found* runs after each report on the scan's worker thread, so it must not touch widgets.
    """

    cameras: list[DiscoveredCamera] = field(default_factory=list)
    error: Exception | None = None
    calls: int = 0
    targets: tuple[str, ...] = ()
    logins: tuple[Login, ...] = ()
    low_impact: bool = False
    on_found: Callable[[], None] = lambda: None
    gate: Event = field(default_factory=Event)
    token: CancelToken | None = None

    def __post_init__(self) -> None:
        self.gate.set()

    def __call__(
        self,
        targets: tuple[str, ...],
        logins: tuple[Login, ...],
        found: Callable[[DiscoveredCamera], None],
        token: CancelToken,
        low_impact: bool = False,
    ) -> list[DiscoveredCamera]:
        self.calls += 1
        self.token = token
        self.targets = targets
        self.logins = logins
        self.low_impact = low_impact
        assert self.gate.wait(5)
        if self.error is not None:
            raise self.error
        for camera in self.cameras:
            found(camera)
            self.on_found()
        return self.cameras


@dataclass
class _Checker:
    """Signing in to a workspace camera with its own login, answering from *cameras* by host, and recording the calls;
    a host not listed does not answer; it waits while *gate* is clear."""

    cameras: dict[str, DiscoveredCamera] = field(default_factory=dict)
    calls: list[tuple[str, Login]] = field(default_factory=list)
    gate: Event = field(default_factory=Event)

    def __post_init__(self) -> None:
        self.gate.set()

    def __call__(self, host: str, login: Login, token: CancelToken) -> DiscoveredCamera | None:
        self.calls.append((host, login))
        assert self.gate.wait(5)
        return self.cameras.get(host)


@dataclass
class _Live:
    qtbot: QtBot
    view: LiveView
    store: WorkspaceStore
    center: SplitView
    scanner: _Scanner
    jobs: BackgroundJobs
    resolutions: _HeldResolver
    controller: LiveViewController
    checker: _Checker

    def wait_for_scan(self) -> None:
        """Wait until the scan under way has finished and the view shows its outcome."""
        self.qtbot.waitUntil(lambda: self.button("Rescan").isEnabled())

    def open_workspace(self, *items: WorkspaceItem) -> None:
        """Make *items* the workspace, opening nothing, as restoring one does."""
        self.store.replace_workspace(Workspace(items=items))

    def rows(self) -> dict[str, str]:
        """Return each listed row's title and its action, empty for none, in order."""
        return {row_texts(row)[0]: row_action(row) for row in shown_rows(self.view.tree)}

    def detail(self, title: str) -> str:
        """Return the detail shown under the row named *title*, with its unbreakable spaces plain."""
        return row(self.view.tree, title).row.detail.replace("\u00a0", " ")

    def press(self, title: str) -> None:
        """Press the action of the row titled *title*."""
        press(self.view.tree, title)

    def button(self, text: str) -> QPushButton:
        [button] = [button for button in self.view.findChildren(QPushButton) if button.text() == text]
        return button

    def ip_range(self) -> QLineEdit:
        return self._field("IP range")

    def _field(self, placeholder: str) -> QLineEdit:
        [found] = [edit for edit in self.view.findChildren(QLineEdit) if edit.placeholderText().startswith(placeholder)]
        return found

    def search(self, text: str) -> None:
        self._field("Search").setText(text)

    def choose(self, button: str, entry: str) -> None:
        """Pick *entry* in the menu of the menu button *button*."""
        [menu_button] = [found for found in self.view.findChildren(MenuButton) if found.text() == button]
        menu = menu_button.menu()
        assert menu is not None
        [action] = [action for action in menu.actions() if action.text() == entry]
        action.trigger()

    def sign_in_choice(self) -> QCheckBox:
        [choice] = self.view.findChildren(QCheckBox)
        return choice

    def save_logins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *logins: Login) -> None:
        """Make *logins* the ones Settings gives the scan, as saving Settings does."""
        monkeypatch.setattr(ConfigManager, "_instance", None)
        config = ConfigManager()
        config.set_config_path(tmp_path / "config.json")
        config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
        defaults = config.get_raw("defaults")
        defaults["network_scan"]["credentials"] = Login.config_value(logins)
        config.set("defaults", defaults)
        self.controller.refresh_defaults()

    def texts(self) -> list[str]:
        return texts(self.view)


@pytest.fixture
def live(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> Generator[_Live, None, None]:
    resolutions = _HeldResolver()
    jobs = BackgroundJobs(max_workers=1)  # One worker runs jobs in the order they were submitted.
    store = WorkspaceStore(resolutions)
    center = SplitView()
    workspace = WorkspaceController(store, center, render_catalog_manager)
    host = QWidget()  # Holds the view, so a test can delete the view alone.
    qtbot.addWidget(host)
    view = LiveView(host)
    qtbot.addWidget(center)
    scanner = _Scanner()
    checker = _Checker()
    controller = LiveViewController(view, workspace, store, jobs, scan=scanner, parent=workspace, check=checker)
    with patch(_LIVE_VIEWER, DummyViewer):
        yield _Live(qtbot, view, store, center, scanner, jobs, resolutions, controller, checker)
    scanner.gate.set()
    checker.gate.set()


def _scan(live: _Live, *cameras: DiscoveredCamera) -> None:
    live.scanner.cameras = list(cameras)
    live.button("Rescan").click()
    live.wait_for_scan()


@pytest.mark.parametrize("startup", [True, False])
@pytest.mark.parametrize("sign_in", [True, False])
def test_launch_scan_keeps_results_for_live_and_allows_rescan(
    qtbot: QtBot,
    tmp_path: Path,
    render_catalog_manager: SceneRenderCatalogManager,
    monkeypatch: pytest.MonkeyPatch,
    startup: bool,
    sign_in: bool,
) -> None:
    monkeypatch.setattr(ConfigManager, "_instance", None)
    config = ConfigManager()
    config.set_config_path(tmp_path / "config.json")
    config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    settings = GlobalSettings()
    settings.scan_on_startup = startup
    settings.scan_sign_in = sign_in
    scanner = _Scanner(cameras=[DiscoveredCamera("192.0.2.12", "AXIS Q1656", "")])
    scanner.gate.clear()
    monkeypatch.setattr("ax_devil.modules.workspace.ui.live_view_controller.scan_network", scanner)
    shortcuts = ShortcutManager()
    shortcuts.register_defaults()
    window = MainWindow(shortcuts, render_catalog_manager)
    qtbot.addWidget(window)
    try:
        window.launch()
        view = window.findChild(LiveView)
        assert view is not None
        rescan = button(view, "Rescan")
        assert view.sign_in_wanted() is sign_in
        if startup:
            qtbot.waitUntil(lambda: scanner.calls == 1)
            assert scanner.low_impact, "automatic discovery uses the lighter scan"
            assert not rescan.isEnabled(), "startup scan continues without opening Live"
        else:
            assert rescan.isEnabled()
            assert scanner.calls == 0
        scanner.gate.set()
        qtbot.waitUntil(rescan.isEnabled)

        window._workspace_session.show_sidebar_view("live")
        if startup:
            assert [row_texts(found)[0] for found in shown_rows(view.tree)] == ["192.0.2.12"]
            assert scanner.calls == 1, "opening Live reuses the startup scan"
        else:
            assert shown_rows(view.tree) == []
        scanner.cameras = [DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "")]
        rescan.click()
        qtbot.waitUntil(rescan.isEnabled)
        assert [row_texts(found)[0] for found in shown_rows(view.tree)] == ["192.0.2.13"]
        assert scanner.calls == (2 if startup else 1)
        assert not scanner.low_impact, "manual Rescan runs at normal speed"
        assert scanner.logins == (Login.saved(config) if sign_in else ())
    finally:
        scanner.gate.set()
        window.close()


def test_saved_sign_in_default_reaches_the_live_scan(live: _Live, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    settings = GlobalSettings()
    settings.scan_sign_in = False
    live.controller.refresh_defaults()
    assert not live.sign_in_choice().isChecked()
    _scan(live)
    assert live.scanner.logins == ()

    settings.scan_sign_in = True
    live.controller.refresh_defaults()
    assert live.sign_in_choice().isChecked()
    live.sign_in_choice().setChecked(False)
    live.controller.refresh_defaults()
    assert not live.sign_in_choice().isChecked(), "unrelated settings saves keep the manual choice"


def test_rows_show_which_cameras_are_streaming_and_follow_their_viewers(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    assert live.rows() == {"Entrance": "Start stream", "192.0.2.11": "Start stream"}
    assert not any(text.endswith("streaming") for text in live.texts())

    live.press("Entrance")
    assert live.center.get_widget_count() == 1
    assert live.rows() == {"Entrance": "Stop stream", "192.0.2.11": "Start stream"}
    assert "1 streaming" in live.texts()

    live.press("192.0.2.11")
    assert live.center.get_widget_count() == 2, "starting a stream never stops another"
    assert "2 streaming" in live.texts()

    live.center.clear_all_widgets()
    assert live.rows() == {"Entrance": "Start stream", "192.0.2.11": "Start stream"}


def test_stop_stream_closes_every_viewer_of_the_camera(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    live.press("Entrance")
    assert live.rows() == {"Entrance": "Stop stream"}

    live.press("Entrance")

    assert live.center.get_widget_count() == 0
    assert live.rows() == {"Entrance": "Start stream"}


def test_a_camera_that_refuses_its_login_warns_and_cannot_start_a_stream(live: _Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)}
    live.open_workspace(
        LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"),
        LiveStreamItem(host="192.0.2.11", label="Dock"),
    )

    live.qtbot.waitUntil(lambda: live.rows()["Entrance"] == "")

    assert "refused" in row(live.view.tree, "Entrance").toolTip(), "the reason is on hover"
    assert live.rows()["Dock"] == "Start stream", "a camera without a login to try is left alone"
    assert live.checker.calls == [("192.0.2.10", Login("root", "pw"))]


def test_a_camera_whose_check_failed_for_another_reason_can_still_start(live: _Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.FAILED)}
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "AXIS Q1656" in live.detail("Entrance"))

    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_a_scan_refusal_does_not_warn_about_a_camera_that_has_its_own_login(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))

    _scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED))

    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_cameras_are_not_signed_in_to_when_signing_in_is_turned_off(
    live: _Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    GlobalSettings().scan_sign_in = False

    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "Entrance" in live.rows())
    assert live.checker.calls == []


def test_a_camera_that_accepts_its_login_shows_what_it_is_without_saying_it_signed_in(live: _Live) -> None:
    live.checker.cameras = {
        "192.0.2.10": DiscoveredCamera(
            "192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN, login=Login("root", "pw")
        )
    }
    live.open_workspace(LiveStreamItem(host="192.0.2.10", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "AXIS Q1656" in live.detail("192.0.2.10"))

    assert live.rows() == {"192.0.2.10": "Start stream"}
    assert "Signed in" not in live.detail("192.0.2.10")


def test_a_camera_can_start_before_its_login_is_checked_and_keeps_stop_when_the_check_refuses_it(live: _Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)}
    live.checker.gate.clear()
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))
    assert live.rows() == {"Entrance": "Start stream"}, "the check does not hold the camera back"

    live.press("Entrance")
    live.checker.gate.set()

    live.qtbot.waitUntil(lambda: "refused" in row(live.view.tree, "Entrance").toolTip())
    assert live.rows() == {"Entrance": "Stop stream"}


def test_what_a_check_found_is_dropped_when_the_camera_was_replaced_meanwhile(live: _Live) -> None:
    live.checker.cameras = {
        "192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED),
        "192.0.2.20": DiscoveredCamera("192.0.2.20", "AXIS P3265-LVE", "", sign_in=SignIn.SIGNED_IN),
    }
    live.checker.gate.clear()
    entrance = LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw")
    live.open_workspace(entrance)
    live.qtbot.waitUntil(lambda: live.checker.calls == [("192.0.2.10", Login("root", "pw"))])
    live.open_workspace(replace(entrance, host="192.0.2.20"))  # Same id, another camera.

    live.checker.gate.set()

    live.qtbot.waitUntil(lambda: "AXIS P3265-LVE" in live.detail("Entrance"))
    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_a_camera_starts_only_with_its_start_button(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))

    live.view.tree.row_activated.emit(row(live.view.tree, "Entrance").row, False)

    assert live.center.get_widget_count() == 0
    live.press("Entrance")
    assert live.center.get_widget_count() == 1


def test_cameras_that_cannot_open_yet_or_at_all_offer_no_action(live: _Live) -> None:
    broken = LiveStreamItem(host="192.0.2.10", label="Dock", overlay_mode=LiveOverlayMode.RTSP, handler_type="nope")
    live.open_workspace(broken)
    live.resolutions.hold = True
    live.store.add_items([LiveStreamItem(host="192.0.2.11", label="Gate")])

    assert live.rows() == {"Dock": "", "Gate": ""}
    assert "nope" in row(live.view.tree, "Dock").toolTip(), "the reason is on hover"

    live.resolutions.finish()
    assert live.rows()["Gate"] == "Stop stream", "an added camera opens once resolved"


def test_nothing_scans_until_rescan_is_pressed(qtbot: QtBot, live: _Live) -> None:
    live.view.window().show()
    qtbot.waitExposed(live.view)
    assert live.scanner.calls == 0
    assert captions(live.view.tree) == []

    _scan(live)

    assert live.scanner.calls == 1
    assert captions(live.view.tree) == ["On the network, 0"]


def test_the_scan_button_waits_and_progress_shows_while_a_scan_runs(live: _Live) -> None:
    progress = live.view.findChild(QProgressBar)
    assert progress is not None
    assert progress.isHidden()
    live.scanner.gate.clear()

    live.button("Rescan").click()
    assert not live.button("Rescan").isEnabled()
    assert not progress.isHidden()
    assert "Scanning…" in live.texts()

    live.scanner.gate.set()
    live.wait_for_scan()

    assert progress.isHidden()
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
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002", "12.6.85", sign_in=SignIn.SIGNED_IN),
    )

    assert captions(live.view.tree) == ["Live, 2", "On the network, 1"]
    assert list(live.rows())[2:] == ["192.0.2.12"]
    assert live.rows()["192.0.2.12"] == "Add"
    assert "AXIS P3265-LVE" in live.detail("192.0.2.12")
    assert "ACCC8E000002" in row(live.view.tree, "192.0.2.12").toolTip()
    assert "12.6.85" in row(live.view.tree, "192.0.2.12").toolTip()

    _scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", ""))

    assert list(live.rows())[2:] == []
    assert "No new cameras" in live.texts()


def test_a_held_camera_the_scan_found_shows_and_is_found_by_what_the_scan_learned(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))

    _scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", "ACCC8E000001", "11.11.73"))

    hover = row(live.view.tree, "Entrance").toolTip()
    assert all(fact in hover for fact in ("AXIS Q1656", "ACCC8E000001", "11.11.73"))
    assert "AXIS Q1656" not in row(live.view.tree, "192.0.2.11").toolTip()
    live.search("q1656")
    assert list(live.rows()) == ["Entrance"]


def test_add_puts_the_camera_in_the_workspace_without_starting_its_stream(
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
    _scan(live, DiscoveredCamera("192.0.2.34", "AXIS P3265-LVE", "", sign_in=SignIn.SIGNED_IN))

    live.press("192.0.2.34")

    [item] = live.store.workspace.items
    assert isinstance(item, LiveStreamItem)
    assert (item.host, item.label, item.username, item.password) == (
        "192.0.2.34",
        "",
        "$CAMERA_USER",
        "$CAMERA_PASS",
    )
    assert (item.camera_head, item.resolution, item.overlay_mode) == (2, "640x480", LiveOverlayMode.RTSP)
    assert live.rows()["192.0.2.34"] == "Start stream"
    assert live.center.get_widget_count() == 0, "adding is not starting the stream"
    assert live.checker.calls == [], "the scan has just shown the login works"
    assert captions(live.view.tree) == ["Live, 1", "On the network, 0"]
    assert "No new cameras" in live.texts()


def test_a_failed_scan_says_so_and_keeps_the_reason_on_hover(live: _Live) -> None:
    live.scanner.error = OSError("Network is unreachable")
    live.button("Rescan").click()
    live.wait_for_scan()

    [failed] = [label for label in live.view.findChildren(QLabel) if label.text() == "Scan failed"]
    assert "Network is unreachable" in failed.toolTip()


def test_a_scan_waiting_its_turn_never_runs_once_the_view_is_gone(qtbot: QtBot, live: _Live) -> None:
    busy, done = Event(), list[object]()
    owner = QObject()
    other = live.jobs.scope(owner)
    other.submit("busy", lambda _job: busy.wait(5), on_result=done.append, on_error=done.append)
    live.button("Rescan").click()  # Queued behind the busy job on the single worker.

    live.view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    busy.set()
    # The single worker reaches this job only after the scan's turn.
    other.submit("after", lambda _job: "after", on_result=done.append, on_error=done.append)
    qtbot.waitUntil(lambda: done == [True, "after"])

    assert live.scanner.calls == 0


def test_closing_the_view_tells_a_running_scan_to_stop(qtbot: QtBot, live: _Live) -> None:
    live.scanner.gate.clear()
    live.button("Rescan").click()
    qtbot.waitUntil(lambda: live.scanner.token is not None)
    assert live.scanner.token is not None
    live.scanner.token.raise_if_cancelled()

    live.controller.cleanup()

    with pytest.raises(Cancelled):
        live.scanner.token.raise_if_cancelled()


def test_a_scan_searches_the_ip_range_in_the_field_and_the_field_starts_as_the_settings_default(live: _Live) -> None:
    assert live.ip_range().text() == ConfigManager().get("defaults")["network_scan"]["ip_range"]

    live.ip_range().setText("10.0.0.5, 10.0.0.0/30")
    live.button("Rescan").click()
    live.wait_for_scan()

    assert live.scanner.targets == ("10.0.0.1", "10.0.0.2", "10.0.0.5")


def test_an_empty_ip_range_scans_the_local_network(live: _Live) -> None:
    live.ip_range().setText("")
    live.button("Rescan").click()
    live.wait_for_scan()

    assert live.scanner.targets == ()
    assert live.scanner.calls == 1


def test_an_invalid_ip_range_scans_nothing_and_says_why_on_hover(live: _Live) -> None:
    live.ip_range().setText("camera")
    live.button("Rescan").click()

    [invalid] = [label for label in live.view.findChildren(QLabel) if label.text() == "Invalid IP range"]
    assert "camera" in invalid.toolTip()
    assert live.scanner.calls == 0
    assert live.button("Rescan").isEnabled()


def test_a_changed_default_reaches_the_field_unless_the_user_typed_another(live: _Live) -> None:
    config = ConfigManager()
    defaults = config.get_raw("defaults")
    default = defaults["network_scan"]["ip_range"]
    controller = live.controller
    defaults["network_scan"]["ip_range"] = "10.1.0.0/24"
    config.set("defaults", defaults)
    try:
        controller.refresh_defaults()
        assert live.ip_range().text() == "10.1.0.0/24"

        live.ip_range().setText("10.2.0.0/24")
        for changed in ("10.2.0.0/24", "10.3.0.0/24"):  # Even a default equal to what was typed is not theirs to take.
            defaults["network_scan"]["ip_range"] = changed
            controller.refresh_defaults()
            assert live.ip_range().text() == "10.2.0.0/24"
    finally:
        defaults["network_scan"]["ip_range"] = default
        config.set("defaults", defaults)


def test_found_cameras_are_listed_while_the_scan_is_still_running(live: _Live) -> None:
    resume = Event()

    def keep_scanning() -> None:
        """Keep the scan under way after its first find."""
        resume.wait(5)

    live.scanner.on_found = keep_scanning
    live.scanner.cameras = [
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", ""),
        DiscoveredCamera("192.0.2.13", "AXIS Q1656", ""),
    ]

    live.button("Rescan").click()
    live.qtbot.waitUntil(lambda: list(live.rows()) == ["192.0.2.12"])
    assert not live.button("Rescan").isEnabled(), "the scan is still running"

    resume.set()
    live.wait_for_scan()

    assert list(live.rows()) == ["192.0.2.12", "192.0.2.13"]


def test_a_burst_of_finds_refreshes_the_list_far_less_often_than_cameras_are_found(
    live: _Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    refreshes = 0
    listing_refresh = live.controller._listing.refresh

    def counted(*args: object) -> None:
        nonlocal refreshes
        refreshes += 1
        listing_refresh(*args)

    monkeypatch.setattr(live.controller._listing, "refresh", counted)
    cameras = [DiscoveredCamera(f"192.0.2.{n}", f"AXIS Q{n}", "") for n in range(1, 101)]

    _scan(live, *cameras)

    assert refreshes < len(cameras) // 4
    assert list(live.rows()) == [camera.host for camera in cameras]


def test_search_finds_workspace_and_found_cameras_by_what_they_show_and_their_details(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002", "12.6.85"),
        DiscoveredCamera("192.0.2.13", "AXIS Q1656", "ACCC8E000003", "11.11.73"),
    )

    live.search("ACCC8E000003")
    assert list(live.rows()) == ["192.0.2.13"]

    live.search("12.6")
    assert list(live.rows()) == ["192.0.2.12"]

    live.search("entrance")
    assert list(live.rows()) == ["Entrance"]

    live.search("")
    assert list(live.rows()) == ["Entrance", "192.0.2.12", "192.0.2.13"]


def test_found_cameras_follow_the_chosen_order(live: _Live) -> None:
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
    )
    assert list(live.rows()) == ["192.0.2.12", "192.0.2.13"]

    live.choose("Sort", "Model")
    assert list(live.rows()) == ["192.0.2.13", "192.0.2.12"]

    live.choose("Sort", "Uptime")
    assert list(live.rows()) == ["192.0.2.12", "192.0.2.13"]


def test_the_filter_hides_found_cameras_by_type_and_the_caption_counts_them(live: _Live) -> None:
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.13", "AXIS C1310-E", "", product_type="Network Speaker"),
    )

    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["192.0.2.12"]
    assert captions(live.view.tree) == ["On the network, 2, 1 hidden"]

    live.choose("Filter", "Network Speaker")
    assert list(live.rows()) == ["192.0.2.12", "192.0.2.13"]


def test_the_filter_hides_held_cameras_by_what_the_scan_learned_too(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))

    _scan(
        live,
        DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.11", "AXIS C1310-E", "", product_type="Network Speaker"),
    )
    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["Entrance"]


def test_trying_to_sign_in_scans_with_the_saved_logins_and_add_uses_the_one_that_worked(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guest, operator = Login("guest", "guest"), Login("operator", "$OPERATOR_PASS")
    live.save_logins(tmp_path, monkeypatch, guest, operator)
    assert live.sign_in_choice().isChecked()

    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN, login=operator),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", sign_in=SignIn.REFUSED),
    )

    assert live.scanner.logins == (guest, operator)
    assert "Signed in" not in live.detail("192.0.2.12")
    assert "Credentials refused" in live.detail("192.0.2.13")
    assert live.rows()["192.0.2.13"] == "", "a camera that did not accept a login cannot be added"
    live.press("192.0.2.12")
    [signed_in] = live.store.workspace.items
    assert isinstance(signed_in, LiveStreamItem)
    assert (signed_in.username, signed_in.password) == ("operator", "$OPERATOR_PASS"), "a reference stays one"


def test_found_camera_row_shows_all_non_secret_information(live: _Live) -> None:
    _scan(
        live,
        DiscoveredCamera(
            "192.0.2.12",
            "AXIS P3265-LVE",
            "ACCC8E000002",
            "12.6.85",
            "Dome Camera",
            93_600.0,
            "aarch64",
            "Ambarella CV25",
            SignIn.SIGNED_IN,
            Login("root", "never-display-this"),
        ),
    )

    camera_row = row(live.view.tree, "192.0.2.12")
    shown = camera_row.row.detail
    detail = shown.replace("\u00a0", " ")
    assert camera_row.row.label == "192.0.2.12"
    assert camera_row.row.wrap_detail
    assert all(
        text in detail
        for text in (
            "AXIS P3265-LVE",
            "Serial ACCC8E000002",
            "Firmware 12.6.85",
            "Type Dome Camera",
            "Up 1d 2h",
            "Architecture aarch64",
            "SoC Ambarella CV25",
        )
    )
    assert "never-display-this" not in detail
    assert "Signed in" not in detail
    [detail_label] = [label for label in camera_row.findChildren(QLabel) if label.text() == shown]
    assert detail_label.wordWrap()


def test_a_scan_without_sign_in_sends_no_credentials(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    live.sign_in_choice().setChecked(False)
    _scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    assert live.scanner.logins == ()


def test_the_logins_show_under_sign_in_and_edits_there_are_saved_for_settings_too(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    [editor] = live.view.findChildren(LoginsEditor)
    assert not editor.isHidden(), "sign-in and its logins are enabled by default"

    live.sign_in_choice().setChecked(False)
    assert editor.isHidden()
    live.sign_in_choice().setChecked(True)
    assert not editor.isHidden()
    button(editor, "Add login").click()
    *_, username, password = [edit for edit in editor.findChildren(QLineEdit) if edit.isVisibleTo(editor)]
    username.setText("operator")
    password.setText("secret")
    password.editingFinished.emit()

    saved = (Login("root", "pass"), Login("operator", "secret"))
    assert Login.saved(ConfigManager()) == saved, "Settings reads the same list"
    _scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    assert live.scanner.logins == saved


@pytest.mark.parametrize(("count", "scrolls"), [(5, False), (7, True)])
def test_up_to_five_logins_show_at_once_and_more_scroll(qtbot: QtBot, count: int, scrolls: bool) -> None:
    editor = LoginsEditor([Login(f"user{index}", "pass") for index in range(count)])
    qtbot.addWidget(editor)
    editor.show()
    QCoreApplication.processEvents()

    scroll = editor.findChild(QScrollArea)
    assert scroll is not None
    assert (scroll.verticalScrollBar().maximum() > 0) is scrolls


def test_an_empty_login_list_takes_no_room(qtbot: QtBot) -> None:
    editor = LoginsEditor([Login("root", "pass")])
    qtbot.addWidget(editor)

    editor.set_logins(())

    scroll = editor.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.height() == 0


def test_right_click_remove_takes_a_camera_out_of_the_workspace(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    live.press("Entrance")
    tree = live.view.tree
    tree.window().show()
    widget = row(tree, "Entrance")
    entries: list[str] = []

    def remove() -> None:
        menu = tree.findChildren(QMenu)[-1]
        entries.extend(action.text() for action in menu.actions() if not action.isSeparator())
        menu.close()
        next(action for action in menu.actions() if action.text() == "Remove").trigger()

    QTimer.singleShot(0, remove)
    tree.customContextMenuRequested.emit(tree.viewport().mapFromGlobal(widget.mapToGlobal(widget.rect().center())))

    assert entries == ["Information", "Rename", "Remove"], "a camera starts only with Start stream"
    assert live.rows() == {"192.0.2.11": "Start stream"}
    assert live.center.get_widget_count() == 0, "removing a camera stops its stream"
