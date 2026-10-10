from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveOverlayMode,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayFile,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    PlaylistResolver,
    PlaylistSettings,
    SeekableVideoContent,
    VideoFileSelection,
    VideoItem,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from tests.helpers.workspace import FakeResolutionContext


class _DialogOptionProvider:
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_FILE", display_name="Test File"),)

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_HANDLER", display_name="Test Handler"),)


def _dialog_context() -> FakeResolutionContext:
    return FakeResolutionContext(WorkspaceIntake(_DialogOptionProvider()))


def _recipe(item: VideoItem | None) -> tuple[str, Path, tuple[OverlayFile, ...]] | None:
    return None if item is None else (item.label, item.video, item.overlays)


def _set_overlay_mode(dialog: AddLiveStreamDialog, mode: LiveOverlayMode) -> None:
    index = dialog._overlay_mode_combo.findData(mode.value)
    dialog._overlay_mode_combo.setCurrentIndex(index)


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
    for mode in (*LiveOverlayMode, LiveOverlayMode.NONE):
        _set_overlay_mode(dialog, mode)
        QApplication.processEvents()
        assert dialog.size() == opening_size
        if not small_window:
            assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
            assert dialog._scroll_area.verticalScrollBar().maximum() == 0
        for index in range(dialog._button_layout.count()):
            item = dialog._button_layout.itemAt(index)
            assert item is not None
            button = item.widget()
            if button is not None:
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        if mode is LiveOverlayMode.MQTT:
            field = dialog._data_source_choice
            dialog._scroll_area.ensureWidgetVisible(field)
            QApplication.processEvents()
            assert (
                dialog._scroll_area.viewport()
                .rect()
                .contains(field.mapTo(dialog._scroll_area.viewport(), field.rect().center()))
            )
    dialog.cleanup()


def test_live_overlay_pages_describe_their_modes(qtbot: QtBot) -> None:
    """The None page explains every mode; each other mode's page starts with its own description."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    for mode in LiveOverlayMode:
        _set_overlay_mode(dialog, mode)
        page = dialog._transport_pages.currentWidget()
        assert page is not None
        texts = [label.text() for label in page.findChildren(QLabel)]
        assert mode.description in texts
        if mode is LiveOverlayMode.NONE:
            assert all(any(other.description in text for text in texts) for other in LiveOverlayMode)
    dialog.cleanup()


@pytest.fixture(autouse=True)
def _stub_live_stream_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved_defaults: dict[str, Any] = {
        "device": {
            "host": "camera.local",
            "username": "root",
            "password": "",
        },
        "live_stream": {
            "rtsp": {
                "camera_head": 1,
                "resolution": "1280x720",
                "data_stream_handler": "ONVIF_XML",
            },
            "analytics-mqtt": {
                "broker_host": "broker.local",
                "broker_username": "mqtt-user",
                "broker_password": "",
                "broker_port": 1883,
                "data_source_key": "analytics/source",
                "data_stream_handler": "ADF_BETA_FRAME",
                "device_api_protocol": "https",
            },
            "analytics-websocket": {
                "topic": "com.axis.scene.frame.v1",
                "channel_id": 2,
                "data_stream_handler": "ADF_BETA_FRAME",
                "device_api_protocol": "http",
            },
            "overlay_source": "none",
        },
    }
    raw_defaults: dict[str, Any] = copy.deepcopy(resolved_defaults)
    raw_defaults["device"]["host"] = "$AX_DEVIL_TARGET_ADDR"
    raw_defaults["device"]["username"] = "$AX_DEVIL_TARGET_USER"
    raw_defaults["device"]["password"] = "$AX_DEVIL_TARGET_PASS"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_host"] = "$AX_DEVIL_MQTT_BROKER_ADDR"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_username"] = "$AX_DEVIL_MQTT_BROKER_USER"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_password"] = "$AX_DEVIL_MQTT_BROKER_PASS"
    # The environment the references name holds the resolved defaults.
    for variable, value in (
        ("AX_DEVIL_TARGET_ADDR", "camera.local"),
        ("AX_DEVIL_TARGET_USER", "root"),
        ("AX_DEVIL_MQTT_BROKER_ADDR", "broker.local"),
        ("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user"),
    ):
        monkeypatch.setenv(variable, value)
    for variable in ("AX_DEVIL_TARGET_PASS", "AX_DEVIL_MQTT_BROKER_PASS"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(
        AddLiveStreamDialog,
        "_load_defaults",
        lambda self: (raw_defaults, resolved_defaults),
    )


@dataclass
class _Discovery:
    """Device responses and queued jobs, leaving the dialog's discovery lifecycle intact."""

    sources: tuple[str, ...] = ("other/source", "analytics/source")
    topics: tuple[str, ...] = ("other.topic", "com.axis.scene.frame.v1")
    jobs: list[QRunnable] = field(default_factory=list)

    def finish(self) -> None:
        """Complete the oldest queued request synchronously."""
        self.jobs.pop(0).run()


@pytest.fixture(autouse=True)
def discovery(monkeypatch: pytest.MonkeyPatch) -> _Discovery:
    """Fake the device and worker scheduler instead of fabricating the loader's private state."""
    pending = _Discovery()
    monkeypatch.setattr(QThreadPool, "start", lambda self, job: pending.jobs.append(job))
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.list_analytics_data_source_keys",
        lambda *_args: pending.sources,
    )
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.list_datahub_topics",
        lambda *_args: pending.topics,
    )
    return pending


class _VideoOptionProvider(_DialogOptionProvider):
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (
            WorkspaceDecoderOption(handler_type="TXT", display_name="Text", file_extensions=(".txt",)),
            WorkspaceDecoderOption(handler_type="FRAME", display_name="Frame", file_extensions=(".jsonl",)),
            WorkspaceDecoderOption(handler_type="TRACKS", display_name="Tracks", file_extensions=(".jsonl",)),
        )


@pytest.fixture
def video_dialog(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> AddVideoDialog:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_video_dialog.default_workspace_intake",
        lambda: WorkspaceIntake(_VideoOptionProvider()),
    )
    dialog = AddVideoDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_add_video_keeps_ok_disabled_and_says_why_until_selection_is_complete(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """Missing input disables OK with an inline reason instead of a silent no-op or an after-the-fact modal."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "overlay.jsonl"
    overlay.write_text("{}\n")
    steps = [
        (video_dialog._video_path_edit, "", "Choose a video file to continue."),
        (video_dialog._video_path_edit, str(tmp_path / "missing.mp4"), "Video file not found."),
        (video_dialog._video_path_edit, str(video), ""),
        (video_dialog._overlay_path_edit, str(tmp_path / "missing.jsonl"), "Overlay file not found."),
        (video_dialog._overlay_path_edit, str(overlay), "Choose the data handler that reads the overlay file."),
    ]
    for edit, text, message in steps:
        edit.setText(text)
        assert video_dialog._message_label.text() == message
        assert video_dialog._ok_button.isEnabled() is not bool(message)

    video_dialog.accept()
    assert video_dialog.get_result() is None

    video_dialog._handler_combo.setCurrentIndex(video_dialog._handler_combo.findData("TRACKS"))
    assert video_dialog._ok_button.isEnabled()
    video_dialog.accept()

    assert _recipe(video_dialog.get_result()) == ("video.mp4", video, (OverlayFile(overlay, "TRACKS"),))


def test_add_video_selects_the_only_decoder_that_reads_the_overlay(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """An overlay extension that one decoder declares picks that handler without a manual choice."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gt.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")

    assert not video_dialog._handler_combo.isEnabled()
    video_dialog._video_path_edit.setText(str(video))
    video_dialog._overlay_path_edit.setText(str(overlay))

    assert video_dialog._handler_combo.isEnabled()
    assert video_dialog._handler_combo.currentData() == "TXT"
    video_dialog._overlay_path_edit.setText(str(tmp_path / "scene.jsonl"))
    assert video_dialog._handler_combo.currentData() is None
    video_dialog._overlay_path_edit.setText(str(overlay))
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"")
    video_dialog._overlay_path_edit.setText(str(pdf))
    assert video_dialog._handler_combo.currentData() is None
    assert not video_dialog._ok_button.isEnabled()
    video_dialog._overlay_path_edit.setText(str(overlay))
    assert video_dialog._handler_combo.currentData() == "TXT"
    assert video_dialog._name_edit.placeholderText() == "clip.mp4"
    video_dialog._name_edit.setText("Gate camera")
    video_dialog.accept()

    assert _recipe(video_dialog.get_result()) == ("Gate camera", video, (OverlayFile(overlay, "TXT"),))


def test_add_video_prefills_a_dropped_selection(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A dropped video and ambiguous overlay open the dialog filled in, waiting only for the handler."""
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_video_dialog.default_workspace_intake",
        lambda: WorkspaceIntake(_VideoOptionProvider()),
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "scene.jsonl"
    overlay.write_text("{}\n")

    dialog = AddVideoDialog(initial=VideoFileSelection(video, overlay))
    qtbot.addWidget(dialog)

    assert dialog._video_path_edit.text() == str(video)
    assert dialog._overlay_path_edit.text() == str(overlay)
    assert dialog._message_label.text() == "Choose the data handler that reads the overlay file."
    assert not dialog._ok_button.isEnabled()


def _blocked_reason(dialog: AddLiveStreamDialog) -> str:
    """Return the inline reason the dialog cannot be accepted, checking that OK is disabled and accept is refused."""
    assert not dialog._ok_button.isEnabled()
    dialog.accept()
    assert dialog.get_result() is None
    assert dialog.result() != AddLiveStreamDialog.DialogCode.Accepted
    return dialog._validation_label.text()


def test_add_live_stream_ok_waits_for_a_host(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured default host, OK stays disabled and says why until a host is entered."""
    monkeypatch.setattr(
        AddLiveStreamDialog,
        "_load_defaults",
        lambda self: ({"device": {}, "live_stream": {}}, {"device": {}, "live_stream": {}}),
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    assert _blocked_reason(dialog) == "Enter the device host."

    dialog._host_edit.setText("camera.local")
    assert dialog._ok_button.isEnabled()
    assert dialog._validation_label.text() == ""

    dialog._host_edit.setText("   ")
    assert _blocked_reason(dialog) == "Enter the device host."


def test_add_live_stream_without_overlay_ignores_the_disabled_handler(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching back to no overlay keeps OK available even though the handler combo still shows a choice."""
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.default_resolution_context",
        _dialog_context,
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    dialog._handler_combo.setCurrentIndex(dialog._handler_combo.findData("TEST_HANDLER"))
    _set_overlay_mode(dialog, LiveOverlayMode.NONE)

    assert dialog._ok_button.isEnabled()
    dialog.accept()
    result = dialog.get_result()
    assert result is not None
    assert (result.overlay_mode, result.handler_type) == (LiveOverlayMode.NONE, None)


def test_add_live_stream_keeps_configured_references_for_fields_left_empty(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty fields take the configured default as written; typed values are kept literally."""
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.default_resolution_context",
        _dialog_context,
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.NONE)
    dialog._password_edit.setText("pa$$word")

    dialog.accept()

    result = dialog.get_result()
    assert result is not None
    assert (result.host, result.username, result.password) == (
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "pa$$word",
    )
    assert result.label == "Live: $AX_DEVIL_TARGET_ADDR"


def test_add_live_stream_requires_handler_when_overlay_mode_is_enabled(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    dialog._host_edit.setText("camera.local")
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    dialog._handler_combo.setCurrentIndex(0)

    assert _blocked_reason(dialog) == "Select a handler type for RTSP Embedded overlays."


def test_add_live_stream_requires_positive_camera_head(qtbot: QtBot) -> None:
    """The validator permits intermediate zero input, but it must not enable OK or create a stream."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    dialog._host_edit.setText("camera.local")
    dialog._camera_head_edit.selectAll()
    QTest.keyClicks(dialog._camera_head_edit, "0")

    assert _blocked_reason(dialog) == "Camera Head must be a positive integer."


def test_add_live_stream_requires_mqtt_host_and_data_source(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    # Override defaults to have empty MQTT host/data source so validation kicks in
    empty_mqtt_defaults = {
        "device": {"host": "camera.local", "username": "root", "password": ""},
        "live_stream": {
            "rtsp": {"camera_head": 1, "resolution": "1280x720", "data_stream_handler": "ONVIF_XML"},
            "analytics-mqtt": {
                "broker_host": "",
                "broker_username": "",
                "broker_password": "",
                "broker_port": 1883,
                "data_source_key": "",
                "data_stream_handler": "ADF_BETA_FRAME",
                "device_api_protocol": "https",
            },
            "overlay_source": "none",
        },
    }
    monkeypatch.setattr(AddLiveStreamDialog, "_load_defaults", lambda self: (empty_mqtt_defaults, empty_mqtt_defaults))

    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    dialog._host_edit.setText("camera.local")
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    dialog._handler_combo.addItem("Test Handler", "TEST_HANDLER")
    dialog._handler_combo.setCurrentIndex(dialog._handler_combo.findData("TEST_HANDLER"))

    assert _blocked_reason(dialog) == "MQTT Host is required for MQTT overlay mode."

    dialog._mqtt_host_edit.setText("broker.local")
    assert _blocked_reason(dialog) == "Data Source is required for MQTT overlay mode."


def test_add_live_stream_shows_defaults_as_placeholders(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    # Env-backed fields show resolved value + source in placeholder, text is empty
    assert dialog._host_edit.text() == ""
    assert "camera.local" in dialog._host_edit.placeholderText()
    assert "$AX_DEVIL_TARGET_ADDR" in dialog._host_edit.placeholderText()

    assert dialog._username_edit.text() == ""
    assert "root" in dialog._username_edit.placeholderText()
    assert "$AX_DEVIL_TARGET_USER" in dialog._username_edit.placeholderText()

    assert dialog._mqtt_host_edit.text() == ""
    assert "broker.local" in dialog._mqtt_host_edit.placeholderText()
    assert "$AX_DEVIL_MQTT_BROKER_ADDR" in dialog._mqtt_host_edit.placeholderText()

    assert dialog._mqtt_username_edit.text() == ""
    assert "mqtt-user" in dialog._mqtt_username_edit.placeholderText()
    assert dialog._mqtt_device_protocol_combo.currentText() == "https"

    # Non-env fields still use setText
    assert dialog._camera_head_edit.text() == "1"
    assert dialog._resolution_edit.text() == "1280x720"


@pytest.mark.parametrize("available", [False, True])
def test_add_live_stream_selects_configured_data_source_only_when_available(
    qtbot: QtBot, discovery: _Discovery, available: bool
) -> None:
    """Device discovery preserves an available configured choice across transport switches."""
    discovery.sources = ("other/source", "analytics/source") if available else ("other/source",)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    discovery.finish()

    expected = "analytics/source" if available else None
    assert dialog._data_source_choice.combo.currentData() == expected
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert dialog._data_source_choice.combo.currentData() == expected
    assert not discovery.jobs


@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
def test_add_live_stream_discards_stale_discovery_and_reloads_after_connection_reverts(
    qtbot: QtBot, discovery: _Discovery, completed: bool
) -> None:
    """Connection changes invalidate both pending responses and loaded choices, even when the host reverts."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    if completed:
        discovery.finish()
        assert dialog._data_source_choice.combo.currentData() == "analytics/source"
    dialog._host_edit.setText("another-camera.local")
    if not completed:
        discovery.finish()

    assert dialog._data_source_choice.combo.currentData() is None
    assert dialog._data_source_choice.combo.currentText() == "Connection changed — refresh data sources"

    dialog._host_edit.clear()
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert len(discovery.jobs) == 1
    discovery.finish()
    assert dialog._data_source_choice.combo.currentData() == "analytics/source"


def test_live_stream_intake_passes_mqtt_protocol_to_overlay_source() -> None:
    result = WorkspaceIntake(_DialogOptionProvider()).create_live_stream(
        host="camera.local",
        display_name="Camera",
        username="root",
        password="",
        overlay_mode=LiveOverlayMode.MQTT,
        handler_type="TEST_HANDLER",
        mqtt_host="broker.local",
        mqtt_username="mqtt-user",
        analytics_data_source_key="analytics/source",
        device_api_protocol="https",
    )

    overlay = result.overlays[0]
    assert isinstance(overlay.source_spec, LiveMQTTOverlaySourceSpec)

    assert overlay.source_spec.device_api_protocol == "https"
    assert overlay.source_spec.broker_username == "mqtt-user"
    assert overlay.source_spec.analytics_data_source_key == "analytics/source"
    assert overlay.display_name == OverlaySourceKind.MQTT_SOURCE.display_name


def test_add_live_stream_websocket_fields_are_visible_and_build_spec(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    discovery: _Discovery,
) -> None:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.default_resolution_context",
        _dialog_context,
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    dialog._host_edit.setText("camera.local")
    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    dialog._handler_combo.setCurrentIndex(dialog._handler_combo.findData("TEST_HANDLER"))
    dialog._mqtt_device_protocol_combo.setCurrentText("http")

    assert dialog._websocket_channel_id_spin.value() == 2
    discovery.finish()

    assert dialog._websocket_topic_choice.combo.currentData() == "com.axis.scene.frame.v1"
    assert dialog._websocket_topic_choice.isVisibleTo(dialog)
    assert dialog._websocket_channel_id_spin.isVisibleTo(dialog)
    assert not dialog._mqtt_host_edit.isVisibleTo(dialog)
    assert not dialog._data_source_choice.isVisibleTo(dialog)
    assert dialog._mqtt_device_protocol_combo.isEnabled()
    assert dialog._ok_button.isEnabled()
    assert dialog._validation_label.text() == ""

    dialog.accept()

    result = dialog.get_result()
    assert result is not None
    [content] = result.resolve(_dialog_context())
    assert isinstance(content, LiveVideoContent)
    assert content.overlays[0].source_spec == LiveWebSocketOverlaySourceSpec(
        handler_type="TEST_HANDLER",
        topic="com.axis.scene.frame.v1",
        channel_id=2,
        device_api_protocol="http",
    )
    assert content.overlays[0].display_name == OverlaySourceKind.WEBSOCKET_SOURCE.display_name


def test_add_live_stream_websocket_topics_show_empty_state(qtbot: QtBot, discovery: _Discovery) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    discovery.topics = ()
    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    discovery.finish()

    assert dialog._websocket_topic_choice.combo.currentData() is None
    assert dialog._websocket_topic_choice.combo.currentText() == "No DataHub topics available"


def test_add_live_stream_applies_transport_protocol_defaults_when_mode_changes(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert dialog._mqtt_device_protocol_combo.currentText() == "https"

    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    assert dialog._mqtt_device_protocol_combo.currentText() == "http"


class _Resolver:
    """Resolve settings naming an existing root into one playlist, and fail for any other root."""

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        if settings.get("root") != "/selected":
            raise ValueError(f"Folder not found: {settings.get('root')}")
        video = SeekableVideoContent(display_name="clip", source_spec=FileVideoSourceSpec(path=Path("/clip.mp4")))
        return [PlaylistContent(display_name="Playlist", entries=(PlaylistEntry(video.standalone_lanes(), True),))]


class _AnyResolverContext(FakeResolutionContext):
    """Resolve every resolver id with the test resolver."""

    def playlist_resolver(self, resolver_id: str) -> PlaylistResolver:
        return _Resolver()


@pytest.fixture
def playlist_dialog(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> AddPlaylistDialog:
    """Return the dialog with every installed resolver's form, resolving through a test resolver."""
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_playlist_dialog.default_resolution_context",
        _AnyResolverContext,
    )
    dialog = AddPlaylistDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_add_playlist_dialog_returns_an_item_from_the_selected_resolver(playlist_dialog: AddPlaylistDialog) -> None:
    """A resolver submitting after the user switched away cannot close the dialog with its settings."""
    settings = playlist_dialog._settings
    first, second = list(settings._resolver_by_widget)[:2]
    second_id, second_name = settings._resolver_by_widget[second]
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(first))
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(second))

    first.submit_settings({"root": "/selected"})
    assert playlist_dialog.result() == AddPlaylistDialog.DialogCode.Rejected
    assert playlist_dialog.get_result() is None

    second.submit_settings({"root": "/selected"})
    assert playlist_dialog.result() == AddPlaylistDialog.DialogCode.Accepted
    result = playlist_dialog.get_result()
    assert result is not None
    assert (result.label, result.resolver, result.settings) == (second_name, second_id, {"root": "/selected"})
    first.submit_settings({"root": "/selected"})
    assert playlist_dialog.get_result() is result


def test_add_playlist_dialog_stays_open_with_the_reason_when_settings_do_not_resolve(
    playlist_dialog: AddPlaylistDialog,
) -> None:
    settings = playlist_dialog._settings
    form = next(iter(settings._resolver_by_widget))
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(form))

    form.submit_settings({"root": "/gone"})

    assert playlist_dialog.result() != AddPlaylistDialog.DialogCode.Accepted
    assert playlist_dialog.get_result() is None
    assert "Folder not found: /gone" in playlist_dialog._message_label.text()


@pytest.mark.parametrize("resolved", ["", "not-a-number", None, "8883"])
def test_live_stream_numeric_defaults_handle_changed_environment(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, resolved: str | None
) -> None:
    defaults = {
        "device": {},
        "live_stream": {
            "overlay_source": "none",
            "rtsp": {"camera_head": resolved},
            "analytics-mqtt": {"broker_port": resolved},
            "analytics-websocket": {"channel_id": resolved},
        },
    }
    monkeypatch.setattr(AddLiveStreamDialog, "_load_defaults", lambda self: (defaults, defaults))
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    assert dialog._camera_head_edit.text() == ("8883" if resolved == "8883" else "1")
    assert dialog._mqtt_port_spin.value() == (8883 if resolved == "8883" else 1883)
    assert dialog._websocket_channel_id_spin.value() == (8883 if resolved == "8883" else 1)
