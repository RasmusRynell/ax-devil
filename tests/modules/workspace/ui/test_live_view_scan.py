"""The Live view's scan controls and the cameras a scan finds."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QProgressBar
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem
from tests.helpers.live_view import Live, scan
from tests.helpers.section_tree import captions, row


def _scan_caption(live: Live) -> SectionCaption:
    [caption] = [found for found in live.view.findChildren(SectionCaption) if found.text() == "Scan settings"]
    return caption


def test_the_scan_button_waits_and_progress_shows_while_a_scan_runs(live: Live) -> None:
    progress = live.view.findChild(QProgressBar)
    assert progress is not None
    assert progress.isHidden()
    live.scanner.gate.clear()

    live.button("Rescan").click()
    assert not live.button("Rescan").isEnabled()
    assert not progress.isHidden()
    assert _scan_caption(live).aside() == "Scanning…"

    live.scanner.gate.set()
    live.wait_for_scan()

    assert progress.isHidden()
    assert _scan_caption(live).aside() == ""


def test_a_scan_lists_only_cameras_the_workspace_does_not_hold(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_DEVIL_TEST_LIVE_CAMERA", "192.0.2.11")
    live.open_workspace(
        LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="$AX_DEVIL_TEST_LIVE_CAMERA")
    )

    scan(
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

    scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", ""))

    assert list(live.rows())[2:] == []
    assert "No new cameras" in live.texts()


def test_a_held_camera_the_scan_found_shows_and_is_found_by_what_the_scan_learned(live: Live) -> None:
    live.open_workspace(LiveStreamItem(host="192.0.2.10", label="Entrance"), LiveStreamItem(host="192.0.2.11"))

    scan(live, DiscoveredCamera("192.0.2.10", "AXIS Q1656", "ACCC8E000001", "11.11.73"))

    hover = row(live.view.tree, "Entrance").toolTip()
    assert all(fact in hover for fact in ("AXIS Q1656", "ACCC8E000001", "11.11.73"))
    assert "AXIS Q1656" not in row(live.view.tree, "192.0.2.11").toolTip()
    live.search("q1656")
    assert list(live.rows()) == ["Entrance"]


def test_add_puts_the_camera_in_the_workspace_without_starting_its_stream(
    live: Live, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    scan(live, DiscoveredCamera("192.0.2.34", "AXIS P3265-LVE", "", sign_in=SignIn.SIGNED_IN))

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


def test_a_failed_scan_says_so_and_keeps_the_reason_on_hover(live: Live) -> None:
    live.scanner.error = OSError("Network is unreachable")
    live.button("Rescan").click()
    live.wait_for_scan()

    failed = _scan_caption(live)
    assert failed.aside() == "Scan failed"
    assert "Network is unreachable" in failed.toolTip()


def test_a_scan_waiting_its_turn_never_runs_once_the_view_is_gone(qtbot: QtBot, live: Live) -> None:
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


def test_closing_the_view_tells_a_running_scan_to_stop(qtbot: QtBot, live: Live) -> None:
    live.scanner.gate.clear()
    live.button("Rescan").click()
    qtbot.waitUntil(lambda: live.scanner.token is not None)
    assert live.scanner.token is not None
    live.scanner.token.raise_if_cancelled()

    live.controller.cleanup()

    with pytest.raises(Cancelled):
        live.scanner.token.raise_if_cancelled()


def test_a_scan_searches_the_ip_range_in_the_field_and_the_field_starts_as_the_settings_default(live: Live) -> None:
    assert live.ip_range().text() == ConfigManager().get("defaults")["network_scan"]["ip_range"]

    live.ip_range().setText("10.0.0.5, 10.0.0.0/30")
    live.button("Rescan").click()
    live.wait_for_scan()

    assert live.scanner.targets == ("10.0.0.1", "10.0.0.2", "10.0.0.5")


def test_an_empty_ip_range_scans_the_local_network(live: Live) -> None:
    live.ip_range().setText("")
    live.button("Rescan").click()
    live.wait_for_scan()

    assert live.scanner.targets == ()
    assert live.scanner.calls == 1


def test_an_invalid_ip_range_scans_nothing_and_says_why_on_hover(live: Live) -> None:
    live.ip_range().setText("camera")
    live.button("Rescan").click()

    invalid = _scan_caption(live)
    assert invalid.aside() == "Invalid IP range"
    assert "camera" in invalid.toolTip()
    assert live.scanner.calls == 0
    assert live.button("Rescan").isEnabled()


def test_a_changed_default_reaches_the_field_unless_the_user_typed_another(live: Live) -> None:
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


def test_found_cameras_are_listed_while_the_scan_is_still_running(live: Live) -> None:
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
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    refreshes = 0
    listing_refresh = live.controller._listing.refresh

    def counted(*args: object) -> None:
        nonlocal refreshes
        refreshes += 1
        listing_refresh(*args)

    monkeypatch.setattr(live.controller._listing, "refresh", counted)
    cameras = [DiscoveredCamera(f"192.0.2.{n}", f"AXIS Q{n}", "") for n in range(1, 101)]

    scan(live, *cameras)

    assert refreshes < len(cameras) // 4
    assert list(live.rows()) == [camera.host for camera in cameras]
