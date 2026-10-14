"""The Live view lists the workspace's cameras with connect and disconnect, and adds cameras found on the network."""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QMenu, QProgressBar, QPushButton, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login, SignIn
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
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
    ) -> list[DiscoveredCamera]:
        self.calls += 1
        self.token = token
        self.targets = targets
        self.logins = logins
        assert self.gate.wait(5)
        if self.error is not None:
            raise self.error
        for camera in self.cameras:
            found(camera)
            self.on_found()
        return self.cameras


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
        """Return the second line of the row named *title*."""
        return row_texts(row(self.view.tree, title))[1]

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
    controller = LiveViewController(view, workspace, store, jobs, scan=scanner, parent=workspace)
    with patch(_LIVE_VIEWER, DummyViewer):
        yield _Live(qtbot, view, store, center, scanner, jobs, resolutions, controller)
    scanner.gate.set()


def _scan(live: _Live, *cameras: DiscoveredCamera) -> None:
    live.scanner.cameras = list(cameras)
    live.button("Rescan").click()
    live.wait_for_scan()


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
    assert "nope" in row(live.view.tree, "Dock").toolTip(), "the reason is on hover"

    live.resolutions.finish()
    assert live.rows()["Gate"] == "Disconnect", "an added camera opens once resolved"


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
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002", "12.6.85"),
    )

    assert captions(live.view.tree) == ["Live, 2", "On the network, 1"]
    assert list(live.rows())[2:] == ["AXIS P3265-LVE"]
    assert live.rows()["AXIS P3265-LVE"] == "Add"
    assert live.detail("AXIS P3265-LVE") == "192.0.2.12"
    assert "ACCC8E000002" in row(live.view.tree, "AXIS P3265-LVE").toolTip()
    assert "12.6.85" in row(live.view.tree, "AXIS P3265-LVE").toolTip()

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
    assert live.rows()["AXIS P3265-LVE"] == "Disconnect"
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
    live.qtbot.waitUntil(lambda: list(live.rows()) == ["AXIS P3265-LVE"])
    assert not live.button("Rescan").isEnabled(), "the scan is still running"

    resume.set()
    live.wait_for_scan()

    assert list(live.rows()) == ["AXIS P3265-LVE", "AXIS Q1656"]


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
    assert list(live.rows()) == [camera.model for camera in cameras]


def test_search_finds_workspace_and_found_cameras_by_what_they_show_and_their_details(live: _Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS P3265-LVE", "ACCC8E000002", "12.6.85"),
        DiscoveredCamera("192.0.2.13", "AXIS Q1656", "ACCC8E000003", "11.11.73"),
    )

    live.search("ACCC8E000003")
    assert list(live.rows()) == ["AXIS Q1656"]

    live.search("12.6")
    assert list(live.rows()) == ["AXIS P3265-LVE"]

    live.search("entrance")
    assert list(live.rows()) == ["Entrance"]

    live.search("")
    assert list(live.rows()) == ["Entrance", "AXIS P3265-LVE", "AXIS Q1656"]


def test_found_cameras_follow_the_chosen_order(live: _Live) -> None:
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", "12.10.1", uptime_seconds=60.0),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", "12.9.4", uptime_seconds=600.0),
    )
    assert list(live.rows()) == ["AXIS Q1656", "AXIS M3086-V"]

    live.choose("Sort", "Model")
    assert list(live.rows()) == ["AXIS M3086-V", "AXIS Q1656"]

    live.choose("Sort", "Uptime")
    assert list(live.rows()) == ["AXIS Q1656", "AXIS M3086-V"]


def test_the_filter_hides_found_cameras_by_type_and_the_caption_counts_them(live: _Live) -> None:
    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", product_type="Box Camera"),
        DiscoveredCamera("192.0.2.13", "AXIS C1310-E", "", product_type="Network Speaker"),
    )

    live.choose("Filter", "Network Speaker")

    assert list(live.rows()) == ["AXIS Q1656"]
    assert captions(live.view.tree) == ["On the network, 2, 1 hidden"]

    live.choose("Filter", "Network Speaker")
    assert list(live.rows()) == ["AXIS Q1656", "AXIS C1310-E"]


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
    live.sign_in_choice().setChecked(True)

    _scan(
        live,
        DiscoveredCamera("192.0.2.12", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN, login=operator),
        DiscoveredCamera("192.0.2.13", "AXIS M3086-V", "", sign_in=SignIn.REFUSED),
    )

    assert live.scanner.logins == (guest, operator)
    assert live.detail("AXIS Q1656") == "192.0.2.12 · Signed in"
    assert live.detail("AXIS M3086-V") == "192.0.2.13 · Credentials refused"
    live.press("AXIS Q1656")
    live.press("AXIS M3086-V")
    signed_in, refused = live.store.workspace.items
    assert isinstance(signed_in, LiveStreamItem) and isinstance(refused, LiveStreamItem)
    assert (signed_in.username, signed_in.password) == ("operator", "$OPERATOR_PASS"), "a reference stays one"
    device_defaults = ConfigManager().get_raw("defaults")["device"]
    assert (refused.username, refused.password) == (device_defaults["username"], device_defaults["password"])


def test_a_scan_without_sign_in_sends_no_credentials(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    _scan(live, DiscoveredCamera("192.0.2.12", "AXIS Q1656", ""))
    assert live.scanner.logins == ()


def test_the_logins_show_under_sign_in_and_edits_there_are_saved_for_settings_too(
    live: _Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.save_logins(tmp_path, monkeypatch, Login("root", "pass"))
    [editor] = live.view.findChildren(LoginsEditor)
    assert editor.isHidden(), "the logins stay out of the way until sign-in is wanted"

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

    assert entries == ["Open", "Open to the Side", "Information", "Rename", "Remove"]
    assert live.rows() == {"192.0.2.11": "Connect"}
    assert live.center.get_widget_count() == 0, "removing a camera disconnects it"
