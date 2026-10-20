"""The Add Live Stream dialog fits its transport pages and builds a Live Stream Item from what is entered."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import LiveMQTTOverlaySourceSpec, LiveOverlayMode, LiveStreamItem, LiveVideoContent
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from tests.helpers.add_dialogs import Discovery, ok_button, select_data, set_overlay_mode, shows_text, use_defaults
from tests.helpers.dialogs import visible_texts
from tests.helpers.forms import form_field

pytestmark = pytest.mark.usefixtures("_stub_live_stream_defaults", "discovery")


@pytest.mark.parametrize("small_window", [False, True])
def test_live_transport_selection_preserves_usable_geometry(qtbot: QtBot, small_window: bool) -> None:
    """Every overlay mode fits the opening size, so selecting one never resizes the window or adds scrolling."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    if small_window:
        dialog.resize(420, 300)
    QApplication.processEvents()
    opening_size = dialog.size()
    fits_the_screen = dialog.height() >= dialog.opening_size_hint().height()  # A small screen scrolls legitimately.
    scroll_area = dialog.findChild(QScrollArea)
    assert scroll_area is not None
    for mode in (*LiveOverlayMode, LiveOverlayMode.NONE):
        set_overlay_mode(dialog, mode)
        QApplication.processEvents()
        assert dialog.size() == opening_size
        if not small_window and fits_the_screen:
            assert scroll_area.horizontalScrollBar().maximum() == 0
            assert scroll_area.verticalScrollBar().maximum() == 0
        for button in dialog.findChildren(QPushButton):
            if button.isVisibleTo(dialog) and not scroll_area.isAncestorOf(button):
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        if mode is LiveOverlayMode.MQTT:
            data_source = form_field(dialog, "Data Source", AnalyticsChoice)
            scroll_area.ensureWidgetVisible(data_source)
            QApplication.processEvents()
            viewport = scroll_area.viewport()
            assert viewport.rect().contains(data_source.mapTo(viewport, data_source.rect().center()))
    dialog.cleanup()


def test_live_overlay_pages_describe_their_modes(qtbot: QtBot) -> None:
    """The None page explains every mode; each other mode's page shows its own description."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    for mode in LiveOverlayMode:
        set_overlay_mode(dialog, mode)
        texts = visible_texts(dialog)
        assert mode.description in texts
        if mode is LiveOverlayMode.NONE:
            assert all(any(other.description in text for text in texts) for other in LiveOverlayMode)
    dialog.cleanup()


def _assert_blocked(dialog: AddLiveStreamDialog, reason: str) -> None:
    """Check that OK is disabled, accepting is refused, and *reason* is shown inline."""
    assert not ok_button(dialog).isEnabled()
    dialog.accept()
    assert dialog.get_result() is None
    assert dialog.result() != AddLiveStreamDialog.DialogCode.Accepted
    assert shows_text(dialog, reason)


def test_add_live_stream_ok_waits_for_a_host(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured default host, OK stays disabled and says why until a host is entered."""
    empty: dict[str, Any] = {"device": {}, "live_stream": {}}
    use_defaults(monkeypatch, empty, empty)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    host = form_field(dialog, "Host", QLineEdit)

    _assert_blocked(dialog, "Enter the device host.")

    host.setText("camera.local")
    assert ok_button(dialog).isEnabled()
    assert not shows_text(dialog, "Enter the device host.")

    host.setText("   ")
    _assert_blocked(dialog, "Enter the device host.")


def test_add_live_stream_without_overlay_ignores_the_disabled_handler(qtbot: QtBot, live_handler: None) -> None:
    """Switching back to no overlay keeps OK available even though the handler combo still shows a choice."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    select_data(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    set_overlay_mode(dialog, LiveOverlayMode.NONE)

    ok_button(dialog).click()
    result = dialog.get_result()
    assert result is not None
    assert isinstance(result.item, LiveStreamItem)
    assert (result.item.overlay_mode, result.item.handler_type) == (LiveOverlayMode.NONE, None)


def test_add_live_stream_keeps_configured_references_for_fields_left_empty(qtbot: QtBot, live_handler: None) -> None:
    """Empty fields take the configured default as written; typed values are kept literally."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    set_overlay_mode(dialog, LiveOverlayMode.NONE)
    form_field(dialog, "Password", QLineEdit).setText("pa$$word")

    dialog.accept()

    result = dialog.get_result()
    assert result is not None
    assert isinstance(result.item, LiveStreamItem)
    assert (result.item.host, result.item.username, result.item.password) == (
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "pa$$word",
    )
    assert result.item.label == "", "the default name is derived from the host, never stored"


def test_analytics_discovery_expands_typed_environment_references(
    qtbot: QtBot, discovery: Discovery, live_handler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery reaches the device with the expanded connection; the item keeps the references as typed."""
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_HOST", "camera.local")
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_USER", "root")
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_PASS", "secret")
    requested: list[tuple[str, ...]] = []

    def fake_list_keys(*args: str) -> tuple[str, ...]:
        requested.append(args)
        return ("analytics/source",)

    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.live_transport_pages.list_analytics_data_source_keys",
        fake_list_keys,
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    form_field(dialog, "Host", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_HOST")
    form_field(dialog, "Username", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_USER")
    form_field(dialog, "Password", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_PASS")

    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    discovery.finish()
    assert requested == [("camera.local", "root", "secret", "https")]

    set_overlay_mode(dialog, LiveOverlayMode.NONE)
    dialog.accept()
    result = dialog.get_result()
    assert result is not None
    assert isinstance(result.item, LiveStreamItem)
    assert (result.item.host, result.item.username, result.item.password) == (
        "$AX_DEVIL_TEST_CAMERA_HOST",
        "$AX_DEVIL_TEST_CAMERA_USER",
        "$AX_DEVIL_TEST_CAMERA_PASS",
    )


def test_add_live_stream_requires_handler_when_overlay_mode_is_enabled(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    form_field(dialog, "Handler Type", QComboBox).setCurrentIndex(0)

    _assert_blocked(dialog, "Select a handler type for RTSP Embedded overlays.")


def test_add_live_stream_requires_positive_camera_head(qtbot: QtBot) -> None:
    """The validator permits intermediate zero input, but it must not enable OK or create a stream."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    camera_head = form_field(dialog, "Camera Head", QLineEdit)
    camera_head.selectAll()
    QTest.keyClicks(camera_head, "0")

    _assert_blocked(dialog, "Camera Head must be a positive integer.")


def test_add_live_stream_mqtt_needs_broker_and_data_source_then_builds_spec(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, live_handler: None, discovery: Discovery
) -> None:
    """MQTT overlays stay blocked until a broker and a discovered data source are chosen."""
    defaults: dict[str, Any] = {
        "device": {"host": "camera.local", "username": "root", "password": ""},
        "live_stream": {"analytics-mqtt": {"device_api_protocol": "https"}, "overlay_source": "none"},
    }
    use_defaults(monkeypatch, defaults, defaults)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    select_data(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    _assert_blocked(dialog, "MQTT Host is required for MQTT overlay mode.")

    form_field(dialog, "MQTT Host", QLineEdit).setText("broker.local")
    form_field(dialog, "MQTT Username", QLineEdit).setText("mqtt-user")
    _assert_blocked(dialog, "Data Source is required for MQTT overlay mode.")

    discovery.finish()
    select_data(form_field(dialog, "Data Source", AnalyticsChoice).combo, "analytics/source")
    ok_button(dialog).click()

    result = dialog.get_result()
    assert result is not None
    [content] = result.contents
    assert isinstance(content, LiveVideoContent)
    assert content.overlays[0].source_spec == LiveMQTTOverlaySourceSpec(
        handler_type="TEST_HANDLER",
        broker_host="broker.local",
        broker_username="mqtt-user",
        analytics_data_source_key="analytics/source",
        device_api_protocol="https",
    )


@pytest.mark.parametrize("clear_resolution", [False, True])
def test_edit_roundtrips_raw_and_inactive_values_without_config_fallback(
    qtbot: QtBot, live_handler: None, monkeypatch: pytest.MonkeyPatch, discovery: Discovery, clear_resolution: bool
) -> None:
    monkeypatch.setenv("EDIT_CAMERA", "camera.local")
    item = LiveStreamItem(
        host="$EDIT_CAMERA",
        username="",
        password="",
        camera_head=3,
        resolution="640x480",
        stream_url="rtsp://custom/live",
        overlay_mode=LiveOverlayMode.RTSP,
        handler_type="TEST_HANDLER",
        mqtt_host="$EDIT_BROKER",
        mqtt_username="",
        mqtt_password="  secret  ",
        mqtt_port=2883,
        analytics_data_source_key="saved/source",
        websocket_topic="saved/topic",
        websocket_channel_id=4,
        device_api_protocol="http",
    )
    dialog = AddLiveStreamDialog(existing=item)
    qtbot.addWidget(dialog)
    form_field(dialog, "Camera Head", QLineEdit).setText("5")
    if clear_resolution:
        form_field(dialog, "Resolution", QLineEdit).clear()
    set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    discovery.finish()
    set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    dialog.accept()

    result = dialog.get_result()
    expected = replace(item, camera_head=5, resolution="1280x720" if clear_resolution else item.resolution)
    assert result is not None and result.item == expected
    assert result.contents[0].item_id == item.id
    dialog.cleanup()


def test_edit_drops_saved_analytics_choices_only_when_the_connection_changes(
    qtbot: QtBot, live_handler: None, discovery: Discovery
) -> None:
    item = LiveStreamItem(host="camera.local", analytics_data_source_key="saved/source", websocket_topic="saved/topic")
    dialog = AddLiveStreamDialog(existing=item)
    qtbot.addWidget(dialog)
    source = form_field(dialog, "Data Source", AnalyticsChoice)
    topic = form_field(dialog, "DataHub Topic", AnalyticsChoice)
    assert source.combo.currentData() == "saved/source" and topic.combo.currentData() == "saved/topic"
    form_field(dialog, "Camera Head", QLineEdit).setText("2")
    assert source.combo.currentData() == "saved/source" and topic.combo.currentData() == "saved/topic"

    form_field(dialog, "Host", QLineEdit).setText("new-camera.local")
    assert source.combo.findData("saved/source") == topic.combo.findData("saved/topic") == -1
    source.refresh.click()
    discovery.finish()
    topic.refresh.click()
    discovery.finish()
    assert source.combo.findData("saved/source") == topic.combo.findData("saved/topic") == -1
    dialog.cleanup()
