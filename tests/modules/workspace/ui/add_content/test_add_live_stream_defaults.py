"""The Add Live Stream dialog starts from the configured defaults and discovers analytics sources on the device."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
)
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from tests.helpers.add_dialogs import Discovery, ok_button, select_data, set_overlay_mode, use_defaults
from tests.helpers.forms import form_field

pytestmark = pytest.mark.usefixtures("_stub_live_stream_defaults", "discovery")


def test_add_live_stream_uses_env_backed_defaults_without_copying_them(qtbot: QtBot) -> None:
    """Env-backed defaults appear as placeholders naming their variable, and an untouched form uses them."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    env_fields = [
        ("Host", "camera.local", "$AX_DEVIL_TARGET_ADDR"),
        ("Username", "root", "$AX_DEVIL_TARGET_USER"),
        ("MQTT Host", "broker.local", "$AX_DEVIL_MQTT_BROKER_ADDR"),
        ("MQTT Username", "mqtt-user", "$AX_DEVIL_MQTT_BROKER_USER"),
    ]
    for label, value, variable in env_fields:
        edit = form_field(dialog, label, QLineEdit)
        assert edit.text() == ""
        assert value in edit.placeholderText()
        assert variable in edit.placeholderText()

    ok_button(dialog).click()
    result = dialog.get_result()
    assert result is not None
    [content] = result.contents
    assert isinstance(content, LiveVideoContent)
    assert content.source_spec == LiveRTSPStreamSpec(host="camera.local", username="root", password="")


@pytest.mark.parametrize("available", [False, True])
def test_add_live_stream_selects_configured_data_source_only_when_available(
    qtbot: QtBot, discovery: Discovery, available: bool
) -> None:
    """Device discovery preserves an available configured choice across transport switches."""
    discovery.sources = ("other/source", "analytics/source") if available else ("other/source",)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    data_source = form_field(dialog, "Data Source", AnalyticsChoice).combo
    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    discovery.finish()

    expected = "analytics/source" if available else None
    assert data_source.currentData() == expected
    set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert data_source.currentData() == expected
    assert not discovery.jobs


@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
def test_add_live_stream_discards_stale_discovery_and_reloads_after_connection_reverts(
    qtbot: QtBot, discovery: Discovery, completed: bool
) -> None:
    """Connection changes invalidate both pending responses and loaded choices, even when the host reverts."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    data_source = form_field(dialog, "Data Source", AnalyticsChoice).combo
    host = form_field(dialog, "Host", QLineEdit)
    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    if completed:
        discovery.finish()
        assert data_source.currentData() == "analytics/source"
    host.setText("another-camera.local")
    if not completed:
        discovery.release_cancelled()

    assert data_source.currentData() is None
    assert data_source.currentText() == "Connection changed — refresh data sources"

    host.clear()
    set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert len(discovery.jobs) == 1
    discovery.finish()
    assert data_source.currentData() == "analytics/source"


def test_add_live_stream_websocket_shows_its_fields_and_builds_spec(
    qtbot: QtBot, live_handler: None, discovery: Discovery
) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    topic = form_field(dialog, "DataHub Topic", AnalyticsChoice)

    set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    select_data(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    discovery.finish()

    assert topic.combo.currentData() == "com.axis.scene.frame.v1"
    assert topic.isVisibleTo(dialog)
    assert form_field(dialog, "Topic Channel", QLineEdit).isVisibleTo(dialog)
    assert not form_field(dialog, "MQTT Host", QLineEdit).isVisibleTo(dialog)
    assert not form_field(dialog, "Data Source", AnalyticsChoice).isVisibleTo(dialog)

    ok_button(dialog).click()

    result = dialog.get_result()
    assert result is not None
    [content] = result.contents
    assert isinstance(content, LiveVideoContent)
    assert content.overlays[0].source_spec == LiveWebSocketOverlaySourceSpec(
        handler_type="TEST_HANDLER",
        topic="com.axis.scene.frame.v1",
        channel_id=2,
        device_api_protocol="http",
    )


def test_add_live_stream_websocket_topics_show_empty_state(qtbot: QtBot, discovery: Discovery) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    topic = form_field(dialog, "DataHub Topic", AnalyticsChoice).combo

    discovery.topics = ()
    set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    discovery.finish()

    assert topic.currentData() is None
    assert topic.currentText() == "No DataHub topics available"


def test_add_live_stream_applies_transport_protocol_defaults_when_mode_changes(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    protocol = form_field(dialog, "Device Protocol", QComboBox)

    set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert protocol.currentText() == "https"

    set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    assert protocol.currentText() == "http"


@pytest.mark.parametrize("resolved", ["", "not-a-number", "8883"])
def test_live_stream_numeric_defaults_handle_changed_environment(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, resolved: str
) -> None:
    """Numeric defaults resolved from an unset or malformed environment fall back instead of breaking the dialog."""
    defaults = {
        "device": {},
        "live_stream": {
            "overlay_source": "none",
            "rtsp": {"camera_head": resolved},
            "analytics-mqtt": {"broker_port": resolved},
            "analytics-websocket": {"channel_id": resolved},
        },
    }
    use_defaults(monkeypatch, defaults, defaults)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    valid = resolved == "8883"
    assert form_field(dialog, "Camera Head", QLineEdit).text() == ("8883" if valid else "1")
    assert form_field(dialog, "MQTT Port", QLineEdit).text() == ("8883" if valid else "1883")
    assert form_field(dialog, "Topic Channel", QLineEdit).text() == ("8883" if valid else "1")
