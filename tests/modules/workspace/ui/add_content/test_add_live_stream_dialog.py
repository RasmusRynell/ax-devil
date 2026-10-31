"""The Add Live Stream dialog fits its transport pages and builds a Live Stream Item from what is entered."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import Any

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.data_sources.live.camera_resolutions import CameraResolutions, parse_camera_resolutions
from ax_devil.modules.workspace.core import LiveMQTTOverlaySourceSpec, LiveOverlayMode, LiveStreamItem, LiveVideoContent
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from ax_devil.modules.workspace.ui.add_content.resolution_field import ResolutionField
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


def test_adding_from_a_camera_starts_from_its_connection_as_entered_and_makes_a_new_stream(
    qtbot: QtBot, live_handler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another stream of a camera needs only its overlay chosen; it is not the stream it was started from."""
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_HOST", "camera.local")
    camera = LiveStreamItem(
        host="$AX_DEVIL_TEST_CAMERA_HOST",
        label="Entrance",
        username="root",
        password="pa$$word",
        device_api_protocol=DeviceProtocol.HTTP,
        device_port=8080,
        rtsp_port=8554,
    )
    dialog = AddLiveStreamDialog(camera=camera)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Add Live Stream"
    assert form_field(dialog, "Name", QLineEdit).text() == ""

    dialog.accept()

    result = dialog.get_result()
    assert result is not None
    item = result.item
    assert isinstance(item, LiveStreamItem)
    assert item.id != camera.id
    connection = ("host", "username", "password", "device_api_protocol", "device_port", "rtsp_port")
    assert [getattr(item, name) for name in connection] == [getattr(camera, name) for name in connection]
    assert item.label == "", "the stream is not named after the one it was started from"


def test_analytics_discovery_expands_typed_environment_references(
    qtbot: QtBot, discovery: Discovery, live_handler: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery reaches the device with the expanded connection; the item keeps the references as typed."""
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_HOST", "camera.local")
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_USER", "root")
    monkeypatch.setenv("AX_DEVIL_TEST_CAMERA_PASS", "secret")
    requested: list[DeviceConnection] = []

    def fake_list_keys(connection: DeviceConnection) -> tuple[str, ...]:
        requested.append(connection)
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
    assert requested == [DeviceConnection("camera.local", DeviceProtocol.HTTPS, 0, "root", "secret", 10)]

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
        "live_stream": {"analytics-mqtt": {}, "overlay_source": "none"},
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
    )


CAMERA = (
    "root.Properties.Image.I0.H264.Resolution=1920x1080,640x480\r\n"
    "root.Properties.Image.I1.H264.Resolution=1024x768,800x600\r\n"
    "root.Image.I0.Appearance.Resolution=640x480\r\n"
    "root.Image.I1.Appearance.Resolution=800x600\r\n"
)


def _camera_answers(monkeypatch: pytest.MonkeyPatch, *answers: str | None) -> tuple[Event, list[str]]:
    """Make the camera give each read the next of *answers* (the last repeats; ``None`` is unreachable).

    Reads wait for the returned gate; the list holds the host each read asked.
    """
    gate, hosts = Event(), list[str]()

    def read(connection: DeviceConnection) -> CameraResolutions:
        assert gate.wait(5)
        hosts.append(connection.host)
        answer = answers[min(len(hosts), len(answers)) - 1]
        if answer is None:
            raise OSError("unreachable")
        return parse_camera_resolutions(answer)

    monkeypatch.setattr("ax_devil.modules.workspace.ui.add_content.resolution_field.read_camera_resolutions", read)
    return gate, hosts


def test_discovery_lists_resolutions_without_replacing_the_camera_default(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery and camera-head changes preserve camera default and text being edited."""
    gate, _hosts = _camera_answers(monkeypatch, CAMERA)
    gate.set()
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    field = form_field(dialog, "Resolution", ResolutionField)
    dialog.show()
    qtbot.waitUntil(lambda: field.itemText(1) == "1920x1080")
    assert field.value() == ""

    form_field(dialog, "Camera Head", QLineEdit).setText("2")
    assert field.itemText(1) == "1024x768"
    assert field.value() == ""
    field.setCurrentIndex(2)
    field.activated.emit(2)
    assert field.value() == "800x600"
    field.setCurrentIndex(0)
    field.activated.emit(0)
    assert field.value() == ""
    dialog.cleanup()

    # A value typed before the camera answers is the user's.
    gate.clear()
    typing = AddLiveStreamDialog()
    qtbot.addWidget(typing)
    field = form_field(typing, "Resolution", ResolutionField)
    typing.show()
    typed = form_field(typing, "Resolution", QLineEdit)
    typed.selectAll()
    QTest.keyClicks(typed, "19")
    typed.setCursorPosition(1)
    gate.set()
    qtbot.waitUntil(lambda: field.itemText(1) == "1920x1080")
    QTest.keyClicks(typed, "x")  # The answer neither replaces the text nor moves the caret.
    assert field.value() == "1x9"
    typing.cleanup()


def test_resolutions_are_read_again_for_a_changed_host_and_after_an_empty_answer(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another host is asked once typing pauses; an answer that lists nothing is asked again when the list opens."""
    monkeypatch.setattr("ax_devil.modules.workspace.ui.add_content.resolution_field._DEBOUNCE_MS", 0)
    gate, hosts = _camera_answers(monkeypatch, CAMERA, "", CAMERA)
    gate.set()
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    field = form_field(dialog, "Resolution", ResolutionField)
    dialog.show()
    qtbot.waitUntil(lambda: field.itemText(1) == "1920x1080")

    form_field(dialog, "Host", QLineEdit).setText("other.local")
    qtbot.waitUntil(lambda: field.itemText(1) == "Camera lists no resolutions")
    assert hosts == ["camera.local", "other.local"]
    field.showPopup()
    qtbot.waitUntil(lambda: field.itemText(1) == "1920x1080")
    qtbot.waitUntil(lambda: field.view().height() >= 2 * field.view().sizeHintForRow(0))  # The open popup grew.
    field.hidePopup()
    dialog.cleanup()


@pytest.mark.parametrize("answer", [CAMERA, None], ids=["listed", "unreachable"])
@pytest.mark.parametrize("resolution", ["", "1280x720"])
def test_saved_resolution_is_kept_whatever_the_camera_answers(
    qtbot: QtBot, live_handler: None, monkeypatch: pytest.MonkeyPatch, answer: str | None, resolution: str
) -> None:
    """A saved camera default or explicit size survives discovery, even when the camera is unreachable."""
    _camera_answers(monkeypatch, answer)[0].set()
    dialog = AddLiveStreamDialog(existing=LiveStreamItem(host="camera.local", resolution=resolution))
    qtbot.addWidget(dialog)
    field = form_field(dialog, "Resolution", ResolutionField)
    dialog.show()
    qtbot.waitUntil(lambda: field.itemText(1) in ("1920x1080", "Could not read resolutions"))

    assert field.value() == resolution
    assert next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save").isEnabled()
    dialog.cleanup()


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
        mqtt_port=2**40,
        analytics_data_source_key="saved/source",
        websocket_topic="saved/topic",
        websocket_channel_id=2**40,
        device_api_protocol=DeviceProtocol.HTTP,
        device_port=8080,
        rtsp_port=8554,
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
    expected = replace(item, camera_head=5, resolution="" if clear_resolution else item.resolution)
    assert result is not None and result.item == expected
    assert result.contents[0].item_id == item.id
    dialog.cleanup()


def test_live_edit_normalizes_changed_connection_fields_but_keeps_password_spaces(qtbot: QtBot) -> None:
    item = LiveStreamItem(host="old-camera.local", stream_url="rtsp://old/live", mqtt_host="old-broker.local")
    dialog = AddLiveStreamDialog(existing=item)
    qtbot.addWidget(dialog)
    for label, text in (
        ("Host", " new-camera.local "),
        ("MQTT Host", " new-broker.local "),
        ("Custom Stream URL", "   "),
        ("Password", " secret "),
    ):
        form_field(dialog, label, QLineEdit).setText(text)
    dialog.accept()
    result = dialog.get_result()
    assert result is not None
    assert result.item == replace(
        item, host="new-camera.local", mqtt_host="new-broker.local", stream_url=None, password=" secret "
    )


@pytest.mark.parametrize("field", ["Host", "MQTT Host"])
def test_live_edit_cannot_save_whitespace_as_a_required_connection(
    qtbot: QtBot, live_handler: None, field: str
) -> None:
    item = LiveStreamItem(
        host="camera.local",
        overlay_mode=LiveOverlayMode.MQTT if field == "MQTT Host" else LiveOverlayMode.NONE,
        handler_type="TEST_HANDLER" if field == "MQTT Host" else None,
        mqtt_host="broker.local",
        analytics_data_source_key="saved/source",
    )
    dialog = AddLiveStreamDialog(existing=item)
    qtbot.addWidget(dialog)
    form_field(dialog, field, QLineEdit).setText("   ")
    dialog.accept()
    assert dialog.get_result() is None
    expected = "Enter the device host." if field == "Host" else "MQTT Host is required for MQTT overlay mode."
    assert shows_text(dialog, expected)
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
