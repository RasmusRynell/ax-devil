from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import (
    FileVideoSourceSpec,
    LiveMQTTOverlaySourceSpec,
    LiveOverlayMode,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
    OverlayFile,
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
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from tests.helpers.forms import form_field
from tests.helpers.workspace import FakeResolutionContext


def _dialog_context() -> FakeResolutionContext:
    return FakeResolutionContext(WorkspaceIntake(_DialogOptionProvider()))


def _recipe(item: VideoItem | None) -> tuple[str, Path, tuple[OverlayFile, ...]] | None:
    return None if item is None else (item.label, item.video, item.overlays)


def _ok(dialog: QWidget) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == "OK")


def _shows(dialog: QWidget, text: str) -> bool:
    return any(label.text() == text for label in dialog.findChildren(QLabel))


def _visible_texts(dialog: QWidget) -> list[str]:
    return [label.text() for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog)]


def _set_overlay_mode(dialog: AddLiveStreamDialog, mode: LiveOverlayMode) -> None:
    combo = form_field(dialog, "Overlay Mode", QComboBox)
    combo.setCurrentIndex(combo.findData(mode.value))


def _select(combo: QComboBox, data: str) -> None:
    combo.setCurrentIndex(combo.findData(data))


class _DialogOptionProvider:
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_FILE", display_name="Test File"),)

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_HANDLER", display_name="Test Handler"),)


@pytest.fixture
def live_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.default_resolution_context",
        _dialog_context,
    )


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
    _use_defaults(monkeypatch, raw_defaults, resolved_defaults)


def _use_defaults(monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any], resolved: dict[str, Any]) -> None:
    """Replace the dialog's config lookup; the dialog has no other seam for configured defaults."""
    monkeypatch.setattr(AddLiveStreamDialog, "_load_defaults", lambda self: (raw, resolved))


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
    scroll_area = dialog.findChild(QScrollArea)
    assert scroll_area is not None
    for mode in (*LiveOverlayMode, LiveOverlayMode.NONE):
        _set_overlay_mode(dialog, mode)
        QApplication.processEvents()
        assert dialog.size() == opening_size
        if not small_window:
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
        _set_overlay_mode(dialog, mode)
        texts = _visible_texts(dialog)
        assert mode.description in texts
        if mode is LiveOverlayMode.NONE:
            assert all(any(other.description in text for text in texts) for other in LiveOverlayMode)
    dialog.cleanup()


class _VideoOptionProvider(_DialogOptionProvider):
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (
            WorkspaceDecoderOption(handler_type="TXT", display_name="Text", file_extensions=(".txt",)),
            WorkspaceDecoderOption(handler_type="FRAME", display_name="Frame", file_extensions=(".jsonl",)),
            WorkspaceDecoderOption(handler_type="TRACKS", display_name="Tracks", file_extensions=(".jsonl",)),
        )


@pytest.fixture
def video_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_video_dialog.default_workspace_intake",
        lambda: WorkspaceIntake(_VideoOptionProvider()),
    )


@pytest.fixture
def video_dialog(qtbot: QtBot, video_intake: None) -> AddVideoDialog:
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
    video_edit = form_field(video_dialog, "Video File", QLineEdit)
    overlay_edit = form_field(video_dialog, "Overlay File", QLineEdit)
    steps = [
        (video_edit, "", "Choose a video file to continue."),
        (video_edit, str(tmp_path / "missing.mp4"), "Video file not found."),
        (video_edit, str(video), ""),
        (overlay_edit, str(tmp_path / "missing.jsonl"), "Overlay file not found."),
        (overlay_edit, str(overlay), "Choose the data handler that reads the overlay file."),
    ]
    for edit, text, message in steps:
        edit.setText(text)
        assert _ok(video_dialog).isEnabled() is not bool(message)
        if message:
            assert _shows(video_dialog, message)

    video_dialog.accept()
    assert video_dialog.get_result() is None

    _select(form_field(video_dialog, "Data Handler", QComboBox), "TRACKS")
    _ok(video_dialog).click()

    assert _recipe(video_dialog.get_result()) == ("video.mp4", video, (OverlayFile(overlay, "TRACKS"),))


def test_add_video_selects_the_only_decoder_that_reads_the_overlay(
    video_dialog: AddVideoDialog, tmp_path: Path
) -> None:
    """An overlay extension that one decoder declares picks that handler without a manual choice."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "gt.txt"
    overlay.write_text("1,1,0,0,1,1,1,-1,-1,-1\n")
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"")
    handler = form_field(video_dialog, "Data Handler", QComboBox)
    overlay_edit = form_field(video_dialog, "Overlay File", QLineEdit)

    assert not handler.isEnabled()
    form_field(video_dialog, "Video File", QLineEdit).setText(str(video))
    overlay_edit.setText(str(overlay))
    assert handler.isEnabled()
    assert handler.currentData() == "TXT"

    overlay_edit.setText(str(tmp_path / "scene.jsonl"))
    assert handler.currentData() is None
    overlay_edit.setText(str(pdf))
    assert handler.currentData() is None
    assert not _ok(video_dialog).isEnabled()

    overlay_edit.setText(str(overlay))
    assert handler.currentData() == "TXT"
    name_edit = form_field(video_dialog, "Display Name", QLineEdit)
    assert name_edit.placeholderText() == "clip.mp4"
    name_edit.setText("Gate camera")
    _ok(video_dialog).click()

    assert _recipe(video_dialog.get_result()) == ("Gate camera", video, (OverlayFile(overlay, "TXT"),))


def test_add_video_prefills_a_dropped_selection(qtbot: QtBot, video_intake: None, tmp_path: Path) -> None:
    """A dropped video and ambiguous overlay open the dialog filled in, waiting only for the handler."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "scene.jsonl"
    overlay.write_text("{}\n")

    dialog = AddVideoDialog(initial=VideoFileSelection(video, overlay))
    qtbot.addWidget(dialog)

    assert not _ok(dialog).isEnabled()
    assert _shows(dialog, "Choose the data handler that reads the overlay file.")
    _select(form_field(dialog, "Data Handler", QComboBox), "FRAME")
    _ok(dialog).click()
    assert _recipe(dialog.get_result()) == ("clip.mp4", video, (OverlayFile(overlay, "FRAME"),))


def _assert_blocked(dialog: AddLiveStreamDialog, reason: str) -> None:
    """Check that OK is disabled, accepting is refused, and *reason* is shown inline."""
    assert not _ok(dialog).isEnabled()
    dialog.accept()
    assert dialog.get_result() is None
    assert dialog.result() != AddLiveStreamDialog.DialogCode.Accepted
    assert _shows(dialog, reason)


def test_add_live_stream_ok_waits_for_a_host(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured default host, OK stays disabled and says why until a host is entered."""
    empty: dict[str, Any] = {"device": {}, "live_stream": {}}
    _use_defaults(monkeypatch, empty, empty)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    host = form_field(dialog, "Host", QLineEdit)

    _assert_blocked(dialog, "Enter the device host.")

    host.setText("camera.local")
    assert _ok(dialog).isEnabled()
    assert not _shows(dialog, "Enter the device host.")

    host.setText("   ")
    _assert_blocked(dialog, "Enter the device host.")


def test_add_live_stream_without_overlay_ignores_the_disabled_handler(qtbot: QtBot, live_handler: None) -> None:
    """Switching back to no overlay keeps OK available even though the handler combo still shows a choice."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    _select(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    _set_overlay_mode(dialog, LiveOverlayMode.NONE)

    _ok(dialog).click()
    result = dialog.get_result()
    assert result is not None
    assert (result.overlay_mode, result.handler_type) == (LiveOverlayMode.NONE, None)


def test_add_live_stream_keeps_configured_references_for_fields_left_empty(qtbot: QtBot, live_handler: None) -> None:
    """Empty fields take the configured default as written; typed values are kept literally."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    _set_overlay_mode(dialog, LiveOverlayMode.NONE)
    form_field(dialog, "Password", QLineEdit).setText("pa$$word")

    dialog.accept()

    result = dialog.get_result()
    assert result is not None
    assert (result.host, result.username, result.password) == (
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "pa$$word",
    )
    assert result.label == "Live: $AX_DEVIL_TARGET_ADDR"


def test_analytics_discovery_expands_typed_environment_references(
    qtbot: QtBot, discovery: _Discovery, live_handler: None, monkeypatch: pytest.MonkeyPatch
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
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.list_analytics_data_source_keys",
        fake_list_keys,
    )
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    form_field(dialog, "Host", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_HOST")
    form_field(dialog, "Username", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_USER")
    form_field(dialog, "Password", QLineEdit).setText("$AX_DEVIL_TEST_CAMERA_PASS")

    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    discovery.finish()
    assert requested == [("camera.local", "root", "secret", "https")]

    _set_overlay_mode(dialog, LiveOverlayMode.NONE)
    dialog.accept()
    result = dialog.get_result()
    assert result is not None
    assert (result.host, result.username, result.password) == (
        "$AX_DEVIL_TEST_CAMERA_HOST",
        "$AX_DEVIL_TEST_CAMERA_USER",
        "$AX_DEVIL_TEST_CAMERA_PASS",
    )


def test_add_live_stream_requires_handler_when_overlay_mode_is_enabled(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
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
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, live_handler: None, discovery: _Discovery
) -> None:
    """MQTT overlays stay blocked until a broker and a discovered data source are chosen."""
    defaults: dict[str, Any] = {
        "device": {"host": "camera.local", "username": "root", "password": ""},
        "live_stream": {"analytics-mqtt": {"device_api_protocol": "https"}, "overlay_source": "none"},
    }
    _use_defaults(monkeypatch, defaults, defaults)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)

    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    _select(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    _assert_blocked(dialog, "MQTT Host is required for MQTT overlay mode.")

    form_field(dialog, "MQTT Host", QLineEdit).setText("broker.local")
    form_field(dialog, "MQTT Username", QLineEdit).setText("mqtt-user")
    _assert_blocked(dialog, "Data Source is required for MQTT overlay mode.")

    discovery.finish()
    _select(form_field(dialog, "Data Source", AnalyticsChoice).combo, "analytics/source")
    _ok(dialog).click()

    result = dialog.get_result()
    assert result is not None
    [content] = result.resolve(_dialog_context())
    assert isinstance(content, LiveVideoContent)
    assert content.overlays[0].source_spec == LiveMQTTOverlaySourceSpec(
        handler_type="TEST_HANDLER",
        broker_host="broker.local",
        broker_username="mqtt-user",
        analytics_data_source_key="analytics/source",
        device_api_protocol="https",
    )


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

    _ok(dialog).click()
    result = dialog.get_result()
    assert result is not None
    [content] = result.resolve(_dialog_context())
    assert isinstance(content, LiveVideoContent)
    assert content.source_spec == LiveRTSPStreamSpec(host="camera.local", username="root", password="")


@pytest.mark.parametrize("available", [False, True])
def test_add_live_stream_selects_configured_data_source_only_when_available(
    qtbot: QtBot, discovery: _Discovery, available: bool
) -> None:
    """Device discovery preserves an available configured choice across transport switches."""
    discovery.sources = ("other/source", "analytics/source") if available else ("other/source",)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    data_source = form_field(dialog, "Data Source", AnalyticsChoice).combo
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    discovery.finish()

    expected = "analytics/source" if available else None
    assert data_source.currentData() == expected
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert data_source.currentData() == expected
    assert not discovery.jobs


@pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
def test_add_live_stream_discards_stale_discovery_and_reloads_after_connection_reverts(
    qtbot: QtBot, discovery: _Discovery, completed: bool
) -> None:
    """Connection changes invalidate both pending responses and loaded choices, even when the host reverts."""
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    data_source = form_field(dialog, "Data Source", AnalyticsChoice).combo
    host = form_field(dialog, "Host", QLineEdit)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    if completed:
        discovery.finish()
        assert data_source.currentData() == "analytics/source"
    host.setText("another-camera.local")
    if not completed:
        discovery.finish()

    assert data_source.currentData() is None
    assert data_source.currentText() == "Connection changed — refresh data sources"

    host.clear()
    _set_overlay_mode(dialog, LiveOverlayMode.RTSP)
    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert len(discovery.jobs) == 1
    discovery.finish()
    assert data_source.currentData() == "analytics/source"


def test_add_live_stream_websocket_shows_its_fields_and_builds_spec(
    qtbot: QtBot, live_handler: None, discovery: _Discovery
) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    topic = form_field(dialog, "DataHub Topic", AnalyticsChoice)

    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    _select(form_field(dialog, "Handler Type", QComboBox), "TEST_HANDLER")
    discovery.finish()

    assert topic.combo.currentData() == "com.axis.scene.frame.v1"
    assert topic.isVisibleTo(dialog)
    assert form_field(dialog, "Topic Channel", QSpinBox).isVisibleTo(dialog)
    assert not form_field(dialog, "MQTT Host", QLineEdit).isVisibleTo(dialog)
    assert not form_field(dialog, "Data Source", AnalyticsChoice).isVisibleTo(dialog)

    _ok(dialog).click()

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


def test_add_live_stream_websocket_topics_show_empty_state(qtbot: QtBot, discovery: _Discovery) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    topic = form_field(dialog, "DataHub Topic", AnalyticsChoice).combo

    discovery.topics = ()
    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
    discovery.finish()

    assert topic.currentData() is None
    assert topic.currentText() == "No DataHub topics available"


def test_add_live_stream_applies_transport_protocol_defaults_when_mode_changes(qtbot: QtBot) -> None:
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    protocol = form_field(dialog, "Device Protocol", QComboBox)

    _set_overlay_mode(dialog, LiveOverlayMode.MQTT)
    assert protocol.currentText() == "https"

    _set_overlay_mode(dialog, LiveOverlayMode.WEBSOCKET)
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
    _use_defaults(monkeypatch, defaults, defaults)
    dialog = AddLiveStreamDialog()
    qtbot.addWidget(dialog)
    valid = resolved == "8883"
    assert form_field(dialog, "Camera Head", QLineEdit).text() == ("8883" if valid else "1")
    assert form_field(dialog, "MQTT Port", QSpinBox).value() == (8883 if valid else 1883)
    assert form_field(dialog, "Topic Channel", QSpinBox).value() == (8883 if valid else 1)


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
