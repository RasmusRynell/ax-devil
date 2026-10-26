"""The Live view scans for cameras, checks their logins, and starts and stops their streams."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login, SignIn
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem, new_item_id
from ax_devil.modules.workspace.ui.browser_rows import RowAction
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamState, LiveStreamStatus
from ax_devil.modules.workspace.ui.live_view import LiveView
from tests.helpers.live_view import Live, Scanner, scan
from tests.helpers.section_tree import captions, expand, row, row_texts, shown_rows
from tests.helpers.widgets import button
from tests.helpers.workspace import DummyViewer


@pytest.mark.parametrize(("startup", "sign_in"), [(True, True), (False, False)])
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
    config.get_raw("defaults")["device"]["protocol"] = "http"
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    settings = GlobalSettings()
    settings.scan_on_startup = startup
    settings.scan_sign_in = sign_in
    scanner = Scanner(cameras=[DiscoveredCamera("192.0.2.12", "AXIS Q1656", "")])
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
            assert scanner.prefer is DeviceProtocol.HTTP, "the scan asks the configured default protocol first"
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


def test_saved_sign_in_default_reaches_the_live_scan(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    settings = GlobalSettings()
    settings.scan_sign_in = False
    live.controller.refresh_defaults()
    assert not live.sign_in_choice().isChecked()
    scan(live)
    assert live.scanner.logins == ()

    settings.scan_sign_in = True
    live.controller.refresh_defaults()
    assert live.sign_in_choice().isChecked()
    live.sign_in_choice().setChecked(False)
    live.controller.refresh_defaults()
    assert not live.sign_in_choice().isChecked(), "unrelated settings saves keep the manual choice"


def test_rows_show_which_cameras_are_streaming_and_follow_their_viewers(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))
    assert live.rows() == {"Entrance": "Start stream", "192.0.2.11": "Start stream"}
    assert not any(text.endswith("streaming") for text in live.texts())

    live.press("Entrance")
    assert live.center.get_widget_count() == 1
    assert live.rows() == {"Entrance": "Stop stream", "192.0.2.11": "Start stream"}
    assert "1 streaming" in live.texts()
    assert live.detail("Entrance") == "192.0.2.10"
    viewer = live.center.get_focused_widget()
    assert isinstance(viewer, DummyViewer)
    viewer.set_stream(LiveStreamStatus(LiveStreamState.RECONNECTING, attempts=2))
    assert live.detail("Entrance") == "192.0.2.10 · Reconnecting (2)"

    live.press("192.0.2.11")
    assert live.center.get_widget_count() == 2, "starting a stream never stops another"
    assert "2 streaming" in live.texts()

    live.center.clear_all_widgets()
    assert live.rows() == {"Entrance": "Start stream", "192.0.2.11": "Start stream"}
    assert live.detail("Entrance") == "192.0.2.10"


def test_stop_stream_closes_every_viewer_of_the_camera(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))
    live.press("Entrance")
    assert live.rows() == {"Entrance": "Stop stream"}

    live.press("Entrance")

    assert live.center.get_widget_count() == 0
    assert live.rows() == {"Entrance": "Start stream"}


def test_a_camera_that_refuses_its_login_warns_and_cannot_start_a_stream(live: Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)}
    entrance = LiveStreamItem(
        host="192.0.2.10",
        label="Entrance",
        username="root",
        password="pw",
        device_api_protocol=DeviceProtocol.HTTP,
        device_port=8080,
    )
    live.open_workspace(
        entrance,
        replace(entrance, id=new_item_id(), label="Entrance overlay"),
        LiveStreamItem(host="192.0.2.11", label="Dock"),
    )

    live.qtbot.waitUntil(lambda: live.rows()["Entrance"] == "" and live.rows()["Entrance overlay"] == "")

    assert "refused" in row(live.view.tree, "Entrance").toolTip(), "the reason is on hover"
    assert live.rows()["Dock"] == "Start stream", "a camera without a login to try is left alone"
    assert live.checker.calls == [(DeviceConnection("192.0.2.10", DeviceProtocol.HTTP, 8080), Login("root", "pw"))], (
        "one connection and login is checked once"
    )


@pytest.mark.parametrize("edit", [False, True], ids=["rescan", "edit"])
def test_a_refused_camera_can_be_checked_again_so_a_fixed_login_is_noticed(live: Live, edit: bool) -> None:
    refused = DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)
    live.checker.cameras = {"192.0.2.10": refused}
    item = LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw")
    live.open_workspace(item)
    live.qtbot.waitUntil(lambda: live.rows()["Entrance"] == "")

    live.checker.cameras = {"192.0.2.10": replace(refused, sign_in=SignIn.SIGNED_IN)}
    if edit:
        live.store.edit_item(replace(item, password="fixed"))
    else:
        scan(live)

    live.qtbot.waitUntil(lambda: len(live.checker.calls) == 2)
    live.qtbot.waitUntil(lambda: live.rows()["Entrance"] == "Start stream")
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_a_camera_whose_check_failed_for_another_reason_can_still_start(live: Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.FAILED)}
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "AXIS Q1656" in live.detail("Entrance"))

    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_a_scan_refusal_does_not_warn_about_a_camera_that_has_its_own_login(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"))

    scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED))

    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_cameras_are_not_signed_in_to_when_signing_in_is_turned_off(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(GlobalSettings, "_instance", None)
    GlobalSettings().scan_sign_in = False

    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "Entrance" in live.rows())
    assert live.checker.calls == []


def test_a_camera_that_accepts_its_login_shows_what_it_is_without_saying_it_signed_in(live: Live) -> None:
    live.checker.cameras = {
        "192.0.2.10": DiscoveredCamera(
            "192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN, login=Login("root", "pw")
        )
    }
    live.open_workspace(LiveStreamItem(host="192.0.2.10", username="root", password="pw"))

    live.qtbot.waitUntil(lambda: "AXIS Q1656" in live.detail("192.0.2.10"))

    assert live.rows() == {"192.0.2.10": "Start stream"}
    assert "Signed in" not in live.detail("192.0.2.10")


def test_a_camera_can_start_before_its_login_is_checked_and_keeps_stop_when_the_check_refuses_it(live: Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)}
    live.checker.gate.clear()
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))
    assert live.rows() == {"Entrance": "Start stream"}, "the check does not hold the camera back"

    live.press("Entrance")
    live.checker.gate.set()

    live.qtbot.waitUntil(lambda: "refused" in row(live.view.tree, "Entrance").toolTip())
    assert live.rows() == {"Entrance": "Stop stream"}


def test_what_a_check_found_is_dropped_when_the_camera_was_replaced_meanwhile(live: Live) -> None:
    live.checker.cameras = {
        "192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED),
        "192.0.2.20": DiscoveredCamera("192.0.2.20", "AXIS P3265-LVE", "", sign_in=SignIn.SIGNED_IN),
    }
    live.checker.gate.clear()
    entrance = LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw")
    live.open_workspace(entrance)
    connection = DeviceConnection("192.0.2.10", entrance.device_api_protocol)
    live.qtbot.waitUntil(lambda: live.checker.calls == [(connection, Login("root", "pw"))])
    live.store.edit_item(replace(entrance, host="192.0.2.20"))

    live.checker.gate.set()

    live.qtbot.waitUntil(lambda: "AXIS P3265-LVE" in live.detail("Entrance"))
    assert live.rows() == {"Entrance": "Start stream"}
    assert "refused" not in row(live.view.tree, "Entrance").toolTip()


def test_a_rescan_replaces_the_checks_still_running_and_their_late_answers_are_dropped(live: Live) -> None:
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.REFUSED)}
    old_gate = live.checker.gate
    old_gate.clear()
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"))
    live.qtbot.waitUntil(lambda: len(live.checker.calls) == 1)
    live.checker.cameras = {"192.0.2.10": DiscoveredCamera("192.0.2.10", "AXIS Q1656", "", sign_in=SignIn.SIGNED_IN)}
    live.checker.gate = Event()
    live.button("Rescan").click()

    old_gate.set()
    live.qtbot.wait(100)

    assert live.rows() == {"Entrance": "Start stream"}, "the older answer says nothing while the newer is awaited"
    live.checker.gate.set()
    live.qtbot.waitUntil(lambda: "AXIS Q1656" in live.detail("Entrance"))
    assert live.rows() == {"Entrance": "Start stream"}


def test_checks_still_queued_for_cameras_the_workspace_dropped_never_contact_them(live: Live) -> None:
    live.checker.gate.clear()
    live.open_workspace(
        LiveStreamItem(host="192.0.2.10", label="Entrance", username="root", password="pw"),
        LiveStreamItem(host="192.0.2.11", label="Dock", username="root", password="pw"),
    )
    live.qtbot.waitUntil(lambda: len(live.checker.calls) == 1)

    live.open_workspace()
    live.checker.gate.set()
    live.qtbot.wait(100)

    assert len(live.checker.calls) == 1, "the check that had not started was cancelled"


def test_a_camera_starts_only_with_its_start_button(live: Live) -> None:
    live.open_workspace(
        LiveStreamItem(host="192.0.2.10", label="Entrance"),
        LiveStreamItem(host="192.0.2.11", label="Dock", overlay_mode=LiveOverlayMode.RTSP, handler_type="ONVIF_XML"),
    )

    expand(live.view.tree, "Dock")
    for title in ("Entrance", "Dock", "RTSP"):
        live.view.tree.row_action_requested.emit(row(live.view.tree, title).row, RowAction.OPEN)

    assert live.center.get_widget_count() == 0, "neither a camera nor its overlay row starts the stream"
    live.press("Entrance")
    assert live.center.get_widget_count() == 1


def test_cameras_that_cannot_open_yet_or_at_all_offer_no_action(live: Live) -> None:
    broken = LiveStreamItem(host="192.0.2.10", label="Dock", overlay_mode=LiveOverlayMode.RTSP, handler_type="nope")
    live.open_workspace(broken)
    live.resolutions.hold = True
    live.store.add_items([LiveStreamItem(host="192.0.2.11", label="Gate")])

    assert live.rows() == {"Dock": "", "Gate": ""}
    assert "nope" in row(live.view.tree, "Dock").toolTip(), "the reason is on hover"

    live.resolutions.finish()
    assert live.rows()["Gate"] == "Stop stream", "an added camera opens once resolved"


def test_nothing_scans_until_rescan_is_pressed(qtbot: QtBot, live: Live) -> None:
    live.view.window().show()
    qtbot.waitExposed(live.view)
    assert live.scanner.calls == 0
    assert captions(live.view.tree) == ["Live, 0"]

    scan(live)

    assert live.scanner.calls == 1
    assert captions(live.view.tree) == ["Live, 0", "On the network, 0"]
