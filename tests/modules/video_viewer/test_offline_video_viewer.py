"""Tests for the offline video viewer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton, QTabWidget, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import SeekableFrameSource
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.viewport_state import NormalizedViewport, ZoomStep
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.draggable import DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer import offline_entry_media, offline_video_viewer, offline_viewer_runtime
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineLane, OfflineSession
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.workspace import (
    ConsiderationItemRef,
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    WorkspaceManager,
)


class _TrackedSignal:
    """Minimal signal stub that records connect/disconnect calls."""

    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: object | None = None) -> None:
        if callback is None:
            self._callbacks.clear()
            return
        try:
            self._callbacks.remove(callback)
        except ValueError:
            return

    def emit(self, *args: object) -> None:
        """Call connected callbacks with *args*."""
        for callback in tuple(self._callbacks):
            callback(*args)  # type: ignore[operator]


class _TrackedFrameSource(SeekableFrameSource):
    """Simple source stub that records shutdown/disposal calls."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.frameReady = cast(Any, _TrackedSignal())
        self.sourceFinished = cast(Any, _TrackedSignal())
        self.stop_calls = 0
        self.wait_timeouts: list[int] = []
        self.delete_later_calls = 0
        self._current_frame = 0
        self._total_frames = 100
        self._playback_speed = 1.0
        self.speed_updates: list[float] = []
        self.async_frame_requests: list[int] = []
        self.fps = 25.0

    def get_total_frames(self) -> int:
        return self._total_frames

    def get_frame_size(self) -> tuple[int, int] | None:
        return (640, 480)

    def get_cached_ranges(self) -> tuple[tuple[int, int], ...]:
        return ()

    def get_duration_s(self) -> float | None:
        return None

    def peek_frame_seconds(self, frame_number: int) -> float | None:
        return None

    def get_current_frame(self) -> int:
        return self._current_frame

    def get_position_generation(self) -> int:
        return 0

    def play(self) -> bool:
        return True

    def pause(self) -> None:
        return

    def stop(self) -> None:
        self.stop_calls += 1

    def wait(self, timeout: int = 2000) -> bool:
        self.wait_timeouts.append(timeout)
        return True

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1

    def jump_to(self, frame_number: int) -> None:
        self._current_frame = frame_number

    def step_delta(self, delta: int) -> None:
        self._current_frame += delta

    def reset_to_start(self) -> None:
        self._current_frame = 0

    def request_frame_async(self, frame_number: int, callback: Callable[[FrameData | None], None]) -> None:
        """Record and immediately complete an async frame request."""
        self.async_frame_requests.append(frame_number)
        self._current_frame = frame_number
        callback(_frame_data(frame_number))

    def set_playback_speed(self, value: float) -> None:
        self._playback_speed = value
        self.speed_updates.append(value)

    def get_playback_speed(self) -> float:
        return self._playback_speed

    def get_timing_profile(self) -> None:
        return None

    def get_frame_timestamps_us(self) -> tuple[int, ...]:
        return tuple(frame_index * 1000 for frame_index in range(self._total_frames))

    def get_frame_timeline(self) -> FrameTimeline:
        return FrameTimeline.lazy(count=self._total_frames, timestamp_loader=self.get_frame_timestamps_us)


class _TrackedOverlaySource:
    """Pull overlay stub that records closing and disposal calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.diagnostics_id = f"test-overlay:{id(self):x}"
        self.close_calls = 0
        self.delete_later_calls = 0
        self.requested_frame_ids: list[FrameIdentifier] = []

    @property
    def handler_type(self) -> str:
        return "tracked"

    def get_filter_config(self) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1

    def moveToThread(self, _thread: object) -> bool:  # noqa: N802
        return True

    def get_total_frames(self) -> int:
        return 100

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        self.requested_frame_ids.append(frame_id)
        return None


class _OverlayDecoderRequest:
    """Decoder-factory payload used by the test FileOverlaySource seam."""

    def __init__(self, name: str, registry: list[_TrackedOverlaySource] | None) -> None:
        self.name = name
        self.registry = registry


class _SpecFileOverlaySource(_TrackedOverlaySource):
    """FileOverlaySource test double created from source specs and decoder lookup."""

    def __init__(
        self,
        path: Path,
        decoder_factory: Callable[[], object],
        handler_type: str,
        **_kwargs: object,
    ) -> None:
        request = decoder_factory()
        name = path.stem
        registry: list[_TrackedOverlaySource] | None = None
        if isinstance(request, _OverlayDecoderRequest):
            name = request.name
            registry = request.registry
        super().__init__(name)
        self.path = path
        self._handler_type = handler_type
        self._timestamp_fallback_policy = TimestampFallbackPolicy()
        self.history_selections: list[tuple[bool, int | None]] = []
        if registry is not None:
            registry.append(self)

    @property
    def handler_type(self) -> str:
        return self._handler_type

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        return self._timestamp_fallback_policy

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        self._timestamp_fallback_policy = policy

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None
    ) -> SceneHistory:
        self.history_selections.append((allow_previous, max_sample_age_us))

        def frame_id(index: int) -> FrameIdentifier:
            return FrameIdentifier(sequence_id=index, timestamp_monotime_us=video_timeline.timestamps_us[index])

        return SceneHistory(
            events=(FrameEvent(frame_id(20), "Delete", f"Delete {self.name}", (self.name,)),),
            objects=(ObjectHistory(self.name, ("human",), ((10, 19),), video_timeline.timestamps_us),),
            frame_count=video_timeline.count,
        )

    def analyze_alignment(self, video_timeline: FrameTimeline) -> OverlayAlignmentReport:
        return OverlayAlignmentReport(
            handler_type=self.handler_type,
            total_video_frames=video_timeline.count,
            total_overlay_frames=0,
            exact_matches=0,
            max_abs_nearest_offset_us=None,
            sample_period_modes_us=(),
            tolerance_us=self._timestamp_fallback_policy.effective_tolerance_us,
            tolerated_past_matches=0,
        )


def _make_tracked_frame_source_opener(
    name: str,
    registry: list[_TrackedFrameSource],
) -> Callable[[], _TrackedFrameSource]:
    """Build a test source opener that appends each created instance to *registry*."""

    def create() -> _TrackedFrameSource:
        source = _TrackedFrameSource(name)
        registry.append(source)
        return source

    return create


_TEST_FRAME_SOURCE_OPENERS: dict[Path, Callable[[], object]] = {}


def _make_overlay_content(
    name: str,
    registry: list[_TrackedOverlaySource] | None = None,
) -> OverlayContent:
    """Build spec-backed overlay content for offline viewer tests."""
    return OverlayContent(
        display_name=name,
        source_spec=FileOverlaySourceSpec(
            path=Path(f"/tmp/{name}.txt"),
            handler_type="TEST_FILE",
            decoder_kwargs={"name": name, "registry": registry},
        ),
        metadata={"path": f"/tmp/{name}.txt", "handler_type": "TEST_FILE"},
    )


def _assert_frame_source_disposed(source: _TrackedFrameSource) -> None:
    """Assert the frame source was stopped, waited, and scheduled for deletion."""

    assert source.stop_calls == 1
    assert source.wait_timeouts == [2000]
    assert source.delete_later_calls == 1


def _assert_overlay_source_disposed(source: _TrackedOverlaySource) -> None:
    """Assert the overlay source was closed and scheduled for deletion."""

    assert source.close_calls == 1
    assert source.delete_later_calls == 1


def _frame_data(frame_number: int) -> FrameData:
    """Build a minimal frame payload for viewer runtime tests."""

    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_monotime_us=float(frame_number * 1000))
    return FrameData(content=QImage(2, 2, QImage.Format.Format_RGB32), frame_id=frame_id, source_id="video")


def _touch_renderer_metrics(widget: OfflineVideoViewerWidget) -> set[str]:
    """Return the diagnostic identities of live display widgets."""

    metric_ids = {display._metrics_instance_id for display in widget.findChildren(FrameViewport)}
    return metric_ids


def _renderer_metric_ids() -> set[str]:
    """Return currently tracked frame viewport metric instance ids."""

    return {viewer.viewer_id for viewer in get_render_metrics_store().snapshot()}


def _count_frame_viewports() -> int:
    """Count live frame viewports across the current Qt application."""

    return sum(isinstance(candidate, FrameViewport) for candidate in QApplication.allWidgets())


def _assert_renderer_metrics_match_live_widgets(
    widget: OfflineVideoViewerWidget, *, baseline_frame_viewports: int | None = None
) -> None:
    """Assert the metrics store only contains ids for currently live renderers."""

    live_metric_ids = _touch_renderer_metrics(widget)
    assert _renderer_metric_ids() == live_metric_ids
    if baseline_frame_viewports is not None:
        assert _count_frame_viewports() == baseline_frame_viewports + len(live_metric_ids)


def _stub_frame_source() -> MagicMock:
    source = MagicMock()
    source.get_total_frames.return_value = 100
    source.fps = 25.0
    source.get_frame_size.return_value = (640, 480)
    source.get_cached_ranges.return_value = ((0, 9),)
    source.get_duration_s.return_value = None
    source.peek_frame_seconds.return_value = None
    source.get_current_frame.return_value = 0
    source.get_playback_speed.return_value = 1.0
    source.get_position_generation.return_value = 0
    source.get_timing_profile.return_value = None
    source.get_frame_timestamps_us.return_value = tuple(frame_index * 1000 for frame_index in range(100))
    source.get_frame_timeline.return_value = FrameTimeline.lazy(
        count=100,
        timestamp_loader=source.get_frame_timestamps_us,
    )
    return source


def _run_now(
    work: Callable[[Callable[[str], None]], Any],
    on_done: Callable[[Any], None],
    *,
    on_progress: Callable[[str], None] = lambda _message: None,
    name: str,
) -> None:
    """Run background work synchronously so viewer tests see each entry opened when the call returns."""
    on_done(work(on_progress))


class _DeferredBackground:
    """Hold background work until the test finishes it, as a slow file open would."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def __call__(
        self,
        work: Callable[[Callable[[str], None]], Any],
        on_done: Callable[[Any], None],
        *,
        on_progress: Callable[[str], None] = lambda _message: None,
        name: str,
    ) -> None:
        self.pending.append(lambda: on_done(work(on_progress)))

    def finish_next(self) -> None:
        """Complete the oldest pending work and deliver its result."""
        self.pending.pop(0)()


@pytest.fixture
def deferred_background(monkeypatch: pytest.MonkeyPatch) -> _DeferredBackground:
    """Make entry opening wait for the test, with releases still running immediately."""
    deferred = _DeferredBackground()

    def release_now(media: EntryMedia) -> None:
        media.close()
        media.delete_later()

    monkeypatch.setattr(offline_entry_media, "run_in_background", deferred)
    monkeypatch.setattr(offline_entry_media, "release_media", release_now)
    monkeypatch.setattr(offline_viewer_runtime, "release_media", release_now)
    monkeypatch.setattr(offline_video_viewer, "release_media", release_now)
    return deferred


@pytest.fixture(autouse=True)
def _stub_spec_source_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    def _create_frame_source(video: SeekableVideoContent) -> object:
        factory = _TEST_FRAME_SOURCE_OPENERS.get(video.source_spec.path)
        if factory is not None:
            return factory()
        return _stub_frame_source()

    def _get_file_decoder_factory(handler_type: str) -> Callable[..., _OverlayDecoderRequest]:
        assert handler_type == "TEST_FILE"

        def _decoder_factory(
            *,
            name: str,
            registry: list[_TrackedOverlaySource] | None = None,
        ) -> _OverlayDecoderRequest:
            return _OverlayDecoderRequest(name, registry)

        return _decoder_factory

    monkeypatch.setattr(offline_entry_media, "create_frame_source", _create_frame_source)
    monkeypatch.setattr(offline_entry_media, "FileOverlaySource", _SpecFileOverlaySource)
    monkeypatch.setattr(offline_entry_media, "get_file_decoder_factory", _get_file_decoder_factory)
    monkeypatch.setattr(offline_entry_media, "run_in_background", _run_now)
    yield
    _TEST_FRAME_SOURCE_OPENERS.clear()


def _make_seekable_content(
    name: str,
    *,
    frame_source_opener: Callable[[], object] = _stub_frame_source,
    overlays: tuple[OverlayContent, ...] = (),
) -> SeekableVideoContent:
    source_spec = FileVideoSourceSpec(path=Path(f"/tmp/{name}.mp4"))
    _TEST_FRAME_SOURCE_OPENERS[source_spec.path] = frame_source_opener
    return SeekableVideoContent(
        display_name=name,
        source_spec=source_spec,
        overlays=overlays,
    )


def _make_local_content(name: str, *, with_overlay: bool = False) -> SeekableVideoContent:
    """Create seekable video content with spec-backed source stubs for testing."""
    overlay_specs: tuple[OverlayContent, ...] = ()
    if with_overlay:
        overlay_specs = (_make_overlay_content("overlay"),)
    return _make_seekable_content(name, overlays=overlay_specs)


def _attach_offline_widget(qtbot: QtBot, widget: OfflineVideoViewerWidget) -> None:
    """Attach an offline viewer in tests and start its workspace lifecycle."""
    qtbot.addWidget(widget)
    widget.on_workspace_attached()


class TestOfflineVideoViewerWidget:
    """Tests for the offline video viewer."""

    @pytest.fixture(autouse=True)
    def _set_render_catalog_manager(self, render_catalog_manager: SceneRenderCatalogManager) -> None:
        self._render_catalog_manager = render_catalog_manager

    def test_display_name_single_video(self, qtbot: QtBot) -> None:
        content = _make_local_content("Solo")
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        assert widget.get_display_name() == "Solo"

    def test_construction_defers_initial_entry_load(self, qtbot: QtBot) -> None:
        content = _make_local_content("Solo")
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        assert widget._runtime is None

        widget.on_workspace_attached()

        assert widget._runtime is not None

    def test_refresh_item_consideration_after_cleanup_is_noop(self, qtbot: QtBot) -> None:
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)
        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        widget.cleanup()
        entry_ref = ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
        widget.refresh_item_consideration(entry_ref, False)

    def test_cleanup_releases_shared_transport_visibility_resources(self, qtbot: QtBot) -> None:
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        controls = widget._global_controls

        widget.cleanup()

        assert controls is not None
        assert controls._cleaned_up

    def test_playlist_navigation(self, qtbot: QtBot) -> None:
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._current_index == 0
        assert widget._nav_label is not None
        assert widget._nav_label.text() == "Entry 1 / 2"
        assert widget._global_controls is not None
        assert widget._navigation_controls is not None
        assert widget._prev_button is not None
        assert widget._next_button is not None
        assert widget._prev_button.accessibleName() == "Previous entry"
        assert widget._next_button.accessibleName() == "Next entry"
        assert widget._global_controls._context_widget is widget._navigation_controls
        assert widget.get_content_layout().indexOf(widget._navigation_controls) == -1

        widget._step_next()
        assert widget._current_index == 1
        assert widget._nav_label.text() == "Entry 2 / 2"

        widget._step_next()
        assert widget._current_index == 1

    def test_navigation_controls_position_stays_stable_while_loading(
        self, qtbot: QtBot, deferred_background: _DeferredBackground
    ) -> None:
        """Playlist navigation controls should not shift while an entry opens."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        deferred_background.finish_next()

        assert widget._nav_label is not None
        nav_bar = widget._nav_label.parentWidget()
        assert nav_bar is not None
        baseline_y = nav_bar.geometry().y()

        widget._step_next()
        QCoreApplication.processEvents()
        assert nav_bar.geometry().y() == baseline_y

        deferred_background.finish_next()
        QCoreApplication.processEvents()
        assert nav_bar.geometry().y() == baseline_y

    def test_playlist_navigation_skips_disabled_entries(self, qtbot: QtBot) -> None:
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        v3 = _make_local_content("C")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v3.standalone_lanes(), default_considered=True),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)
        workspace_manager.set_item_considered(
            ConsiderationItemRef.playlist_entry(playlist.content_id, 1),
            False,
        )
        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        widget._step_next()
        assert widget._current_index == 2

        widget._step_prev()
        assert widget._current_index == 0

    def test_playlist_starts_on_first_enabled_entry(self, qtbot: QtBot) -> None:
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)
        workspace_manager.set_item_considered(
            ConsiderationItemRef.playlist_entry(playlist.content_id, 0),
            False,
        )
        widget = OfflineVideoViewerWidget(
            playlist,
            start_index=0,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        assert widget._current_index == 1

    def test_play_at_eof_restarts(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        content = _make_seekable_content("Solo", frame_source_opener=lambda: source)
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        source.reset_to_start.assert_not_called()
        source.get_current_frame.return_value = 99

        widget._start_playback()
        source.reset_to_start.assert_called_once()

    def test_single_video_composes_frame_display_and_seekable_controls(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        content = _make_seekable_content("Solo", frame_source_opener=lambda: source)
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        displays = widget.findChildren(FrameDisplay)
        controls = widget.findChildren(SeekableVideoControlPanel)

        assert len(displays) == 1
        assert len(controls) == 1

    def test_single_video_pane_names_it_once_and_describes_it_in_the_header(self, qtbot: QtBot) -> None:
        """A lane named like its pane shows no name on the video; the header shows the video's size, rate and length."""
        source = _stub_frame_source()
        source.get_duration_s.return_value = 4.0
        content = _make_seekable_content("Solo", frame_source_opener=lambda: source)
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        widget.resize(900, 600)
        widget.show()

        assert widget.findChild(QLabel, "lane-indicator-label") is None
        assert widget.header_details() == "640×480 · 25 fps · 0:04"
        controls = widget.findChildren(SeekableVideoControlPanel)[0]
        assert controls.timeline_slider.cached_ranges() == ((0, 9),)

    def test_single_video_seekable_controls_route_to_workflow_actions(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        content = _make_seekable_content("Solo", frame_source_opener=lambda: source)
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        controls = widget.findChildren(SeekableVideoControlPanel)[0]

        controls.frameStepRequested.emit(10)
        controls.jumpToRequested.emit(42)
        controls.playRequested.emit()
        controls.pauseRequested.emit()
        controls.set_playback_speed(1.1)

        source.jump_to.assert_any_call(10)
        source.jump_to.assert_any_call(42)
        source.play.assert_called()
        source.pause.assert_called()
        source.set_playback_speed.assert_any_call(1.1)

    @patch("ax_devil.modules.video_viewer.offline_video_viewer.QMessageBox.warning")
    def test_navigation_failure_warns_and_stays_on_failed_entry(self, mock_warning: MagicMock, qtbot: QtBot) -> None:
        """A failed entry leaves the viewer empty on it, with its opened sources released, and stepping still works."""
        opened: list[_TrackedFrameSource] = []

        def broken_open() -> object:
            raise RuntimeError("broken entry")

        v1 = _make_local_content("A")
        v2 = _make_seekable_content(
            "B", frame_source_opener=_make_tracked_frame_source_opener("B", opened), overlays=()
        )
        v3 = _make_seekable_content("C", frame_source_opener=broken_open)
        entry_with_failure = PlaylistEntry(
            lanes=(*v2.standalone_lanes(), *v3.standalone_lanes()),
            default_considered=True,
        )
        playlist = PlaylistContent(
            display_name="Test",
            entries=(PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True), entry_with_failure),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        widget._step_next()

        assert widget._current_index == 1
        assert widget._runtime is None
        mock_warning.assert_called_once()
        assert "broken entry" in mock_warning.call_args.args[2]
        assert len(opened) == 1
        _assert_frame_source_disposed(opened[0])

        widget._step_prev()

        assert widget._current_index == 0
        assert widget._runtime is not None

    def test_navigating_while_loading_opens_the_newest_entry(
        self, qtbot: QtBot, deferred_background: _DeferredBackground
    ) -> None:
        """Stepping again while an entry opens replaces it: the older entry opens nothing more and is never shown."""
        sources: dict[str, list[_TrackedFrameSource]] = {"A": [], "B": [], "C": []}
        contents = [
            _make_seekable_content(name, frame_source_opener=_make_tracked_frame_source_opener(name, registry))
            for name, registry in sources.items()
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(
                PlaylistEntry(lanes=content.standalone_lanes(), default_considered=True) for content in contents
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        deferred_background.finish_next()
        first_runtime = widget._runtime
        assert first_runtime is not None

        widget._step_next()
        assert widget._runtime is None
        _assert_frame_source_disposed(sources["A"][0])
        widget._step_next()
        assert widget._current_index == 2
        assert widget._nav_label is not None
        assert widget._nav_label.text() == "Entry 3 / 3"

        deferred_background.finish_next()
        assert widget._runtime is None
        assert sources["B"] == []

        deferred_background.finish_next()
        runtime = widget._runtime
        assert runtime is not None
        assert runtime.get_primary_video_source() is sources["C"][0]
        assert sources["C"][0].stop_calls == 0

    def test_cleanup_while_loading_never_installs_the_entry(
        self, qtbot: QtBot, deferred_background: _DeferredBackground
    ) -> None:
        """Closing the viewer mid-open does not wait for the open, and the entry is never shown or left open."""
        opened: list[_TrackedFrameSource] = []
        content = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        widget.cleanup()
        deferred_background.finish_next()

        assert widget._runtime is None
        assert all(source.stop_calls == 1 for source in opened)

    @patch("ax_devil.modules.video_viewer.offline_video_viewer.QMessageBox.warning")
    def test_display_build_failure_warns_and_releases_the_entry(self, mock_warning: MagicMock, qtbot: QtBot) -> None:
        """A failure while building an entry's displays is reported like a failed open, with its sources released."""
        opened: list[_TrackedFrameSource] = []
        content = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        with patch.object(OfflineSession, "build", side_effect=RuntimeError("broken display")):
            widget.on_workspace_attached()

        assert widget._runtime is None
        assert "broken display" in mock_warning.call_args.args[2]
        assert len(opened) == 1
        _assert_frame_source_disposed(opened[0])

    def test_shared_video_multi_overlay(self, qtbot: QtBot) -> None:
        """Single video with multiple overlays creates multiple lanes sharing one video source."""
        source = _stub_frame_source()
        source.get_total_frames.return_value = 123
        content = _make_seekable_content(
            "multi-overlay",
            frame_source_opener=lambda: source,
            overlays=(
                _make_overlay_content("first"),
                _make_overlay_content("second"),
            ),
        )
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert content.overlays[0].source_spec is not None
        assert isinstance(content.overlays[0].source_spec, FileOverlaySourceSpec)
        assert content.overlays[0].source_spec.handler_type == "TEST_FILE"
        assert content.overlays[0].source_spec.decoder_kwargs["name"] == "first"
        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 2
        assert widget._runtime.video_source_count() == 1
        assert widget._global_controls is not None
        assert widget._use_global_controls
        assert not widget._global_controls.isHidden()
        assert all(lane.controls is None for lane in widget._runtime.lanes)
        assert widget._global_controls.timeline_slider.maximum() == 122
        source.jump_to.assert_called_with(0)
        source.play.assert_called()

    def test_shared_video_multi_overlay_lanes_follow_one_timeline(self, qtbot: QtBot) -> None:
        """Shared-video lanes should present the same primary frame index."""
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []
        content = _make_seekable_content(
            "multi-overlay",
            frame_source_opener=_make_tracked_frame_source_opener("video", frame_sources),
            overlays=(
                _make_overlay_content("first", overlay_sources),
                _make_overlay_content("second", overlay_sources),
            ),
        )
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._runtime is not None
        assert len(frame_sources) == 1
        assert len(overlay_sources) == 2

        frame_sources[0].frameReady.emit(_frame_data(7))
        QCoreApplication.processEvents()

        assert widget._runtime.current_frame == 7
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [7, 7]

        widget._jump_to_frame(12)
        frame_sources[0].frameReady.emit(_frame_data(12))
        QCoreApplication.processEvents()

        assert widget._runtime.current_frame == 12
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [12, 12]

        widget._step_frames(3)
        frame_sources[0].frameReady.emit(_frame_data(15))
        QCoreApplication.processEvents()

        assert widget._runtime.current_frame == 15
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [15, 15]

        widget.set_playback_speed(1.7)

        assert frame_sources[0].speed_updates[-1] == 1.7

    def test_lane_history_follows_playback_and_jumps_to_requested_frames(self, qtbot: QtBot) -> None:
        """Each lane lists its overlay's history, follows the displayed frame and seeks on request."""
        frame_sources: list[_TrackedFrameSource] = []
        content = _make_seekable_content(
            "events",
            frame_source_opener=_make_tracked_frame_source_opener("video", frame_sources),
            overlays=(_make_overlay_content("first"), _make_overlay_content("second")),
        )
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        assert widget._runtime is not None
        event_logs = [lane.tools_panel.event_log for lane in widget._runtime.lanes if lane.tools_panel is not None]
        assert [log._model.data(log._model.index(0, 0)) for log in event_logs] == [
            "#20 00:00:00.020 Delete first",
            "#20 00:00:00.020 Delete second",
        ]

        widget.show()
        for side_panel in widget.findChildren(DraggablePanel):
            side_panel.expand()
        for tabs in widget.findChildren(QTabWidget, "mediaToolsTabs"):
            tabs.setCurrentIndex(1)
        assert all(log.isVisible() for log in event_logs)

        event_logs[1].frameRequested.emit(20)
        assert frame_sources[0].get_current_frame() == 20
        frame_sources[0].frameReady.emit(_frame_data(20))
        QCoreApplication.processEvents()

        assert widget._runtime.current_frame == 20
        qtbot.waitUntil(lambda: [log._model.position_row() for log in event_logs] == [0, 0])

        panels = [lane.tools_panel for lane in widget._runtime.lanes if lane.tools_panel is not None]
        panels[0].frameRequested.emit(12)
        assert frame_sources[0].get_current_frame() == 12

    def test_lane_history_follows_sticky_and_fallback_settings(self, qtbot: QtBot) -> None:
        """Object presence is placed again whenever lookup would select samples differently."""
        overlay_sources: list[_TrackedOverlaySource] = []
        content = _make_seekable_content("history", overlays=(_make_overlay_content("only", overlay_sources),))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        assert widget._runtime is not None
        (lane,) = widget._runtime.lanes
        assert lane.tools_panel is not None
        (source,) = overlay_sources
        assert isinstance(source, _SpecFileOverlaySource)
        assert source.history_selections == [(True, 2_050_000)]

        lane.tools_panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=False))
        widget._runtime._set_lane_timestamp_fallback_policy(lane, TimestampFallbackPolicy(tolerance_us=0))

        assert source.history_selections == [(True, 2_050_000), (False, None), (False, None)]

    @pytest.mark.parametrize("lane_count, columns", [(1, 1), (2, 2), (3, 2), (5, 3)])
    def test_lane_geometry(self, qtbot: QtBot, lane_count: int, columns: int) -> None:
        """Lanes fill equal cells, wrap into rows and retain usable widths when the viewer shrinks."""
        playlist = PlaylistContent(
            display_name="comparison",
            entries=(
                PlaylistEntry(
                    lanes=tuple(
                        EntryLane(
                            display_name=f"Lane {index}",
                            video=_make_local_content(f"Video {index}"),
                            default_considered=True,
                        )
                        for index in range(lane_count)
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        widget.show()
        assert widget._runtime is not None
        container = widget._runtime.container
        panes = [lane.display.parentWidget() for lane in widget._runtime.lanes]
        assert all(pane is not None for pane in panes)
        for width in (1200, 400):
            widget.resize(width, 700)
            QApplication.processEvents()
            rects = [pane.geometry() for pane in panes if pane is not None]
            assert max(rect.width() for rect in rects) - min(rect.width() for rect in rects) <= 1
            assert all(container.rect().contains(rect) for rect in rects)
            assert all(lane.display.width() >= lane.display.minimumSizeHint().width() for lane in widget._runtime.lanes)
            for index, rect in enumerate(rects):
                assert rect.y() == rects[index // columns * columns].y()
                if index % columns:
                    assert rect.x() > rects[index - 1].right()
                if index >= columns:
                    assert rect.y() > rects[index - columns].bottom()

    def test_no_considered_lanes_placeholder(self, qtbot: QtBot) -> None:
        """An entry with all lanes excluded still shows its placeholder."""
        parent = QWidget()
        qtbot.addWidget(parent)
        runtime = OfflineSession.build(parent, EntryMedia(), render_catalog_manager=self._render_catalog_manager)
        label = runtime.container.findChild(QLabel)
        assert label is not None
        assert label.text() == "No considered lanes in this entry"
        runtime.cleanup()

    def test_multi_video_entry(self, qtbot: QtBot) -> None:
        """PlaylistEntry with multiple videos renders through the lane pipeline."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=v1, default_considered=True),
                        EntryLane(display_name="B", video=v2, default_considered=True),
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 2
        assert widget._runtime.video_source_count() == 2
        assert widget._global_controls is not None
        assert widget._use_global_controls
        assert not widget._global_controls.isHidden()
        assert all(lane.controls is None for lane in widget._runtime.lanes)

        lane_indicators = []
        for lane in widget._runtime.lanes:
            parent = lane.display.parentWidget()
            if parent is None:
                continue
            lane_indicators.append(parent.findChild(QLabel, "lane-indicator-label"))
        indicator_texts = [label.text() for label in lane_indicators if label is not None]
        assert indicator_texts == ["A", "B"]

        widget.toggle_info_overlay()

        assert all(lane.display.viewport._overlay_visible for lane in widget._runtime.lanes)  # noqa: SLF001
        assert all(label is not None and label.isHidden() for label in lane_indicators)

        widget.toggle_info_overlay()

        assert all(label is not None and not label.isHidden() for label in lane_indicators)

    def test_keyboard_zoom_preserves_fullscreen_lane_center(self, qtbot: QtBot) -> None:
        """Zoom uses the focused lane's pan even when a smaller peer has clamped it away."""
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=_make_local_content("A"), default_considered=True),
                        EntryLane(display_name="B", video=_make_local_content("B"), default_considered=True),
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        widget.resize(700, 1000)
        widget.show()
        widget.activateWindow()
        qtbot.waitUntil(widget.isActiveWindow)
        assert widget._runtime is not None
        first, second = widget._runtime.displays
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0)
        frame = VideoFrameWithOverlays(VideoFrame(image, 0.0, 0), None)
        first.display_frame(frame)
        second.display_frame(frame)
        fullscreen = LaneFullscreenController(widget, [])
        second.viewport.setFocus()
        fullscreen.toggle()
        qtbot.waitUntil(second.window().isActiveWindow)
        qtbot.waitUntil(second.viewport.hasFocus)
        try:
            anchor = QPointF(second.viewport.width() / 2, 80)
            for _ in range(6):
                second.viewport.wheelEvent(
                    QWheelEvent(
                        anchor,
                        anchor,
                        QPoint(),
                        QPoint(0, 120),
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase,
                        False,
                    )
                )
            base = second.viewport.frame_display_rect()
            assert base is not None
            before = second.viewport.viewport_state.to_normalized(base)
            assert before.pan_y != 0
            assert first.viewport.viewport_state.pan_offset.y() == 0

            widget.zoom(ZoomStep.IN)

            after = second.viewport.viewport_state.to_normalized(base)
            assert after.zoom > before.zoom
            assert after.pan_y == pytest.approx(before.pan_y)
            assert first.viewport.viewport_state.zoom_level == after.zoom

            widget.zoom(ZoomStep.RESET)

            for display in (first, second):
                assert display.viewport.viewport_state.zoom_level == 1.0
                assert display.viewport.viewport_state.pan_offset == QPointF(0, 0)
        finally:
            fullscreen.exit()
            widget.cleanup()

    def test_lane_names_hide_while_zoomed_and_follow_preference(self, qtbot: QtBot) -> None:
        """Zooming any lane hides every lane name until the view is fitted again; the preference hides them all."""
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=_make_local_content("A"), default_considered=True),
                        EntryLane(display_name="B", video=_make_local_content("B"), default_considered=True),
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        assert widget._runtime is not None
        names = [lane.display.viewport.findChild(QLabel, "lane-indicator-label") for lane in widget._runtime.lanes]
        assert [name.text() for name in names if name is not None] == ["A", "B"]

        def shown() -> list[bool]:
            return [name is not None and not name.isHidden() for name in names]

        first_viewport = widget._runtime.lanes[0].display.viewport
        image = QImage(640, 480, QImage.Format.Format_RGB32)
        image.fill(0)
        first_viewport.display_frame(VideoFrameWithOverlays(VideoFrame(image=image, timestamp=0.0), overlays=None))
        first_viewport.wheelEvent(
            QWheelEvent(
                QPointF(100, 80),
                QPointF(100, 80),
                QPoint(),
                QPoint(0, 120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
        )
        assert shown() == [False, False]

        for lane in widget._runtime.lanes:
            lane.display.set_viewport(NormalizedViewport(zoom=1.0, pan_x=0.0, pan_y=0.0))
        assert shown() == [True, True]

        settings = GlobalSettings()
        settings.set_overlay_enabled(OverlayPreference.LANE_NAMES, False)
        try:
            assert shown() == [False, False]
            widget.toggle_info_overlay()
            widget.toggle_info_overlay()
            assert shown() == [False, False]
        finally:
            settings.set_overlay_enabled(OverlayPreference.LANE_NAMES, True)
        assert shown() == [True, True]

    def test_multi_video_lanes_follow_primary_frame_index(self, qtbot: QtBot) -> None:
        """Distinct video lanes should asynchronously render the primary timeline frame."""
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []

        overlay_a = _make_overlay_content("overlay-a", overlay_sources)
        overlay_b = _make_overlay_content("overlay-b", overlay_sources)
        v1 = _make_seekable_content(
            "A",
            frame_source_opener=_make_tracked_frame_source_opener("A", frame_sources),
            overlays=(overlay_a,),
        )
        v2 = _make_seekable_content(
            "B",
            frame_source_opener=_make_tracked_frame_source_opener("B", frame_sources),
            overlays=(overlay_b,),
        )
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(
                            display_name="A",
                            video=v1,
                            default_considered=True,
                            overlay=overlay_a,
                        ),
                        EntryLane(
                            display_name="B",
                            video=v2,
                            default_considered=True,
                            overlay=overlay_b,
                        ),
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._runtime is not None
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2

        frame_sources[0].frameReady.emit(_frame_data(7))
        QCoreApplication.processEvents()

        assert widget._runtime.current_frame == 7
        assert frame_sources[1].async_frame_requests[-1] == 7
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [7, 7]

    def test_comparison_lanes_are_visible_without_overlap(self, qtbot: QtBot) -> None:
        """Every comparison lane remains visible with its own usable display area."""
        content = _make_seekable_content(
            "multi-overlay",
            overlays=(
                _make_overlay_content("first"),
                _make_overlay_content("second"),
                _make_overlay_content("third"),
            ),
        )
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._runtime is not None
        widget.resize(1200, 900)
        widget.show()
        QApplication.processEvents()
        displays = [lane.display for lane in widget._runtime.lanes]
        assert len(displays) == 3
        bounds = [display.rect().translated(display.mapTo(widget, display.rect().topLeft())) for display in displays]
        for display, bound in zip(displays, bounds):
            assert display.isVisible()
            assert bound.width() > 0 and bound.height() > 0
            assert widget.rect().contains(bound)
        for index, first in enumerate(bounds):
            assert all(not first.intersects(second) for second in bounds[index + 1 :])

    def test_lane_consideration_refresh_rebuilds_current_entry(self, qtbot: QtBot) -> None:
        """Toggling lane consideration updates the active entry layout."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=v1, default_considered=True),
                        EntryLane(display_name="B", video=v2, default_considered=True),
                    ),
                    default_considered=True,
                ),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)

        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)
        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 2

        lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 1)
        workspace_manager.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 1

    def test_lane_consideration_reenable_disposes_multi_video_runtime_sources(self, qtbot: QtBot) -> None:
        """Rebuilding a compare entry must fully dispose prior per-lane sources."""
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []

        overlay_a = _make_overlay_content("overlay-a", overlay_sources)
        overlay_b = _make_overlay_content("overlay-b", overlay_sources)
        v1 = _make_seekable_content(
            "A",
            frame_source_opener=_make_tracked_frame_source_opener("A", frame_sources),
            overlays=(overlay_a,),
        )
        v2 = _make_seekable_content(
            "B",
            frame_source_opener=_make_tracked_frame_source_opener("B", frame_sources),
            overlays=(overlay_b,),
        )
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(
                            display_name="A",
                            video=v1,
                            default_considered=True,
                            overlay=overlay_a,
                        ),
                        EntryLane(
                            display_name="B",
                            video=v2,
                            default_considered=True,
                            overlay=overlay_b,
                        ),
                    ),
                    default_considered=True,
                ),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)

        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2

        lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, 1)
        workspace_manager.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 1
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 3
        _assert_frame_source_disposed(frame_sources[0])
        _assert_frame_source_disposed(frame_sources[1])
        _assert_overlay_source_disposed(overlay_sources[0])
        _assert_overlay_source_disposed(overlay_sources[1])

        workspace_manager.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 2
        assert len(frame_sources) == 5
        assert len(overlay_sources) == 5
        _assert_frame_source_disposed(frame_sources[2])
        _assert_overlay_source_disposed(overlay_sources[2])

    def test_video_lane_reenable_disposes_shared_video_runtime_sources(self, qtbot: QtBot) -> None:
        """Rebuilding shared-video compare lanes must dispose both shared and overlay sources."""
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []

        content = _make_seekable_content(
            "multi-overlay",
            frame_source_opener=_make_tracked_frame_source_opener("video", frame_sources),
            overlays=(
                _make_overlay_content("first", overlay_sources),
                _make_overlay_content("second", overlay_sources),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(content)

        widget = OfflineVideoViewerWidget(
            content,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        assert len(frame_sources) == 1
        assert len(overlay_sources) == 2
        assert widget._runtime is not None
        assert widget._runtime.video_source_count() == 1

        lane_ref = ConsiderationItemRef.video_lane(content.content_id, 1)
        workspace_manager.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 1
        assert widget._runtime.video_source_count() == 1
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 3
        _assert_frame_source_disposed(frame_sources[0])
        _assert_overlay_source_disposed(overlay_sources[0])
        _assert_overlay_source_disposed(overlay_sources[1])

        workspace_manager.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert len(widget._runtime.lanes) == 2
        assert widget._runtime.video_source_count() == 1
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 5
        _assert_frame_source_disposed(frame_sources[1])
        _assert_overlay_source_disposed(overlay_sources[2])

    def test_playlist_shared_overlay_toggles_prune_stale_renderer_metrics(self, qtbot: QtBot) -> None:
        """Playlist overlay toggles should not accumulate dead renderer metrics."""
        metrics_store = get_render_metrics_store()
        metrics_store.clear()

        video = _make_seekable_content(
            "video",
            overlays=(
                _make_overlay_content("o1"),
                _make_overlay_content("o2"),
                _make_overlay_content("o3"),
            ),
        )
        playlist = PlaylistContent(
            display_name="playlist",
            entries=(
                PlaylistEntry(
                    lanes=tuple(
                        EntryLane(
                            display_name=overlay.display_name,
                            video=video,
                            overlay=overlay,
                            default_considered=True,
                        )
                        for overlay in video.overlays
                    ),
                    default_considered=True,
                ),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)
        baseline_frame_viewports = _count_frame_viewports()

        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        try:
            _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            for lane_index in (1, 2):
                lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, lane_index)
                workspace_manager.set_item_considered(lane_ref, False)
                widget.refresh_item_consideration(lane_ref, False)
                QCoreApplication.processEvents()
                _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            for lane_index in (1, 2):
                lane_ref = ConsiderationItemRef.playlist_lane(playlist.content_id, 0, lane_index)
                workspace_manager.set_item_considered(lane_ref, True)
                widget.refresh_item_consideration(lane_ref, True)
                QCoreApplication.processEvents()
                _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            widget.cleanup()
            QCoreApplication.processEvents()
            assert _renderer_metric_ids() == set()
            assert _count_frame_viewports() == baseline_frame_viewports
        finally:
            metrics_store.clear()

    def test_video_overlay_toggles_prune_stale_renderer_metrics(self, qtbot: QtBot) -> None:
        """Standalone multi-overlay toggles should not accumulate dead renderer metrics."""
        metrics_store = get_render_metrics_store()
        metrics_store.clear()

        content = _make_seekable_content(
            "video",
            overlays=(
                _make_overlay_content("o1"),
                _make_overlay_content("o2"),
                _make_overlay_content("o3"),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(content)
        baseline_frame_viewports = _count_frame_viewports()

        widget = OfflineVideoViewerWidget(
            content,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)

        try:
            _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            for lane_index in (1, 2):
                lane_ref = ConsiderationItemRef.video_lane(content.content_id, lane_index)
                workspace_manager.set_item_considered(lane_ref, False)
                widget.refresh_item_consideration(lane_ref, False)
                QCoreApplication.processEvents()
                _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            for lane_index in (1, 2):
                lane_ref = ConsiderationItemRef.video_lane(content.content_id, lane_index)
                workspace_manager.set_item_considered(lane_ref, True)
                widget.refresh_item_consideration(lane_ref, True)
                QCoreApplication.processEvents()
                _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            widget.cleanup()
            QCoreApplication.processEvents()
            assert _renderer_metric_ids() == set()
            assert _count_frame_viewports() == baseline_frame_viewports
        finally:
            metrics_store.clear()

    def test_entry_navigation_prunes_stale_renderer_metrics(self, qtbot: QtBot) -> None:
        """Offline entry rebuilds should keep renderer metrics scoped to live widgets only."""
        metrics_store = get_render_metrics_store()
        metrics_store.clear()

        first = _make_local_content("A", with_overlay=True)
        second = _make_seekable_content(
            "B",
            overlays=(
                _make_overlay_content("o1"),
                _make_overlay_content("o2"),
            ),
        )
        playlist = PlaylistContent(
            display_name="playlist",
            entries=(
                PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
            ),
        )
        baseline_frame_viewports = _count_frame_viewports()

        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        try:
            _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            widget._step_next()
            QCoreApplication.processEvents()
            _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            widget._step_prev()
            QCoreApplication.processEvents()
            _assert_renderer_metrics_match_live_widgets(widget, baseline_frame_viewports=baseline_frame_viewports)

            widget.cleanup()
            QCoreApplication.processEvents()
            assert _renderer_metric_ids() == set()
            assert _count_frame_viewports() == baseline_frame_viewports
        finally:
            metrics_store.clear()

    def test_switching_to_multi_lane_keeps_controls_over_viewer(self, qtbot: QtBot) -> None:
        """Global controls should overlay the active viewer when switching entries."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="mixed",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=v1, default_considered=True),
                        EntryLane(display_name="B", video=v2, default_considered=True),
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        widget.resize(800, 600)
        widget.show()
        QCoreApplication.processEvents()

        widget._step_next()
        QCoreApplication.processEvents()

        assert widget._runtime is not None
        assert widget._global_controls is not None
        assert widget._viewer_host_layout is not None
        assert widget._viewer_host_layout.indexOf(widget._runtime.container) >= 0
        assert widget._viewer_host_layout.indexOf(widget._global_controls) >= 0
        assert widget.get_content_layout().indexOf(widget._global_controls) == -1
        assert widget._viewer_host is not None
        assert widget._runtime.container.geometry() == widget._viewer_host.rect()
        assert widget._global_controls.geometry().bottom() == widget._viewer_host.rect().bottom()
        assert widget._global_controls._hover_sink is widget._global_control_visibility

    def test_switching_to_single_lane_keeps_playlist_transport_over_viewer(self, qtbot: QtBot) -> None:
        """Multi-entry playlists should keep one overlay transport when the lane count changes."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="mixed",
            entries=(
                PlaylistEntry(
                    lanes=(
                        EntryLane(display_name="A", video=v1, default_considered=True),
                        EntryLane(display_name="B", video=v2, default_considered=True),
                    ),
                    default_considered=True,
                ),
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._global_controls is not None
        assert widget._use_global_controls

        widget._step_next()

        assert widget._runtime is not None
        assert widget._use_global_controls
        assert widget._global_controls is not None
        assert not widget._global_controls.isHidden()
        assert widget._runtime.primary_controls is None
        assert widget._viewer_host_layout is not None
        assert widget._viewer_host_layout.indexOf(widget._runtime.container) >= 0
        assert widget._viewer_host_layout.indexOf(widget._global_controls) >= 0

    def test_excluding_current_playlist_entry_navigates_to_next_considered(self, qtbot: QtBot) -> None:
        """Excluding the active playlist entry should move the viewer to a valid entry."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        workspace_manager = WorkspaceManager()
        workspace_manager.add_content(playlist)

        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_manager,
            render_catalog_manager=self._render_catalog_manager,
        )
        _attach_offline_widget(qtbot, widget)
        assert widget._current_index == 0

        entry_ref = ConsiderationItemRef.playlist_entry(playlist.content_id, 0)
        workspace_manager.set_item_considered(entry_ref, False)
        widget.refresh_item_consideration(entry_ref, False)
        QCoreApplication.processEvents()

        assert widget._current_index == 1

    def test_multi_lane_global_speed_control_updates_shared_source(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        content = _make_seekable_content(
            "multi-overlay",
            frame_source_opener=lambda: source,
            overlays=(
                _make_overlay_content("first"),
                _make_overlay_content("second"),
            ),
        )
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        assert widget._global_controls is not None
        widget._global_controls.set_playback_speed(1.1)

        assert source.set_playback_speed.call_args_list[-1].args == (1.1,)

    def test_selected_playback_speed_is_reapplied_after_entry_switch(self, qtbot: QtBot) -> None:
        frame_sources: list[_TrackedFrameSource] = []

        v1 = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", frame_sources))
        v2 = _make_seekable_content("B", frame_source_opener=_make_tracked_frame_source_opener("B", frame_sources))
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        widget.set_playback_speed(1.8)
        widget._step_next()

        assert frame_sources[-1].speed_updates[-1] == 1.8

    def test_overlay_visibility_survives_navigation_and_is_snapshotted_for_export(self, qtbot: QtBot) -> None:
        """Rebuilt playlist lanes keep preferences; an export retains its selected compiled variant."""
        first = _make_local_content("first", with_overlay=True)
        second = _make_local_content("second", with_overlay=True)
        playlist = PlaylistContent(
            display_name="Playlist",
            entries=(
                PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
            ),
        )
        widget = OfflineVideoViewerWidget(playlist, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        def details(lane: OfflineLane) -> tuple[QCheckBox, QPushButton]:
            assert lane.tools_panel is not None
            confidence = lane.tools_panel.findChild(QCheckBox, "overlayFeature_confidence")
            reset = lane.tools_panel.findChild(QPushButton, "overlayVisibilityReset")
            assert confidence is not None and reset is not None
            return confidence, reset

        assert widget._runtime is not None
        lane = widget._runtime.lanes[0]
        assert lane.presenter is not None
        full = lane.presenter.scene_render_catalog
        confidence, _reset = details(lane)
        widget._runtime.pause_playback()
        with patch.object(lane.display, "refresh_overlays", wraps=lane.display.refresh_overlays) as refresh:
            confidence.setChecked(False)
            refresh.assert_called_once()
        assert not widget._runtime.is_playing
        assert lane.presenter.scene_render_catalog is not full
        widget._step_next()
        assert widget._runtime is not None
        lane = widget._runtime.lanes[0]
        confidence, reset = details(lane)
        assert not confidence.isChecked()
        assert lane.presenter is not None
        frozen = lane.presenter.scene_render_catalog
        with patch("ax_devil.modules.video_viewer.export.ExportDialog") as dialog_type:
            dialog_type.return_value.__enter__.return_value.exec.side_effect = reset.click
            widget._runtime.export_lanes()
            export_lanes = dialog_type.call_args.args[0]
        assert export_lanes[0].presenter.scene_render_catalog is frozen
        assert lane.presenter.scene_render_catalog is not frozen
        widget._step_prev()
        assert widget._runtime is not None
        confidence, _reset = details(widget._runtime.lanes[0])
        assert confidence.isChecked()

    def test_rapid_playback_speed_transitions_update_source_immediately(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        content = _make_seekable_content("Solo", frame_source_opener=lambda: source)
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)

        widget.set_playback_speed(0.1)
        widget.set_playback_speed(8.0)
        widget.set_playback_speed(0.2)

        assert source.set_playback_speed.call_args_list[-3].args == (0.1,)
        assert source.set_playback_speed.call_args_list[-2].args == (8.0,)
        assert source.set_playback_speed.call_args_list[-1].args == (0.2,)


class TestMediaToolsToggle:
    """The Tools toggle opens the focused lane's media tools; new viewers and entries start with them closed."""

    @pytest.fixture(autouse=True)
    def _set_render_catalog_manager(self, render_catalog_manager: SceneRenderCatalogManager) -> None:
        self._render_catalog_manager = render_catalog_manager

    def _open_viewer(self, qtbot: QtBot, content: SeekableVideoContent | PlaylistContent) -> OfflineVideoViewerWidget:
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        _attach_offline_widget(qtbot, widget)
        return widget

    def test_tools_toggle_opens_the_focused_lane_and_tracks_drag_handle_changes(self, qtbot: QtBot) -> None:
        content = _make_seekable_content(
            "lanes", overlays=(_make_overlay_content("first"), _make_overlay_content("second"))
        )
        widget = self._open_viewer(qtbot, content)
        widget.show()
        widget.activateWindow()
        qtbot.waitUntil(widget.isActiveWindow)
        assert widget._runtime is not None
        displays = widget._runtime.displays

        assert [display.is_side_panel_open() for display in displays] == [False, False]

        displays[1].viewport.setFocus()
        qtbot.waitUntil(displays[1].viewport.hasFocus)
        widget.toggle_media_tools()

        assert [display.is_side_panel_open() for display in displays] == [False, True]

        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in displays] == [False, False]

        # With focus outside the videos, the toggle acts on the first lane.
        widget.setFocus()
        qtbot.waitUntil(widget.hasFocus)
        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in displays] == [True, False]

    def test_next_playlist_entry_starts_with_tools_closed(self, qtbot: QtBot) -> None:
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=(
                PlaylistEntry(lanes=_make_local_content("A").standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=_make_local_content("B").standalone_lanes(), default_considered=True),
            ),
        )
        widget = self._open_viewer(qtbot, playlist)
        widget.toggle_media_tools()
        assert widget._runtime is not None
        assert [display.is_side_panel_open() for display in widget._runtime.displays] == [True]

        widget._step_next()

        assert widget._current_index == 1 and widget._runtime is not None
        assert [display.is_side_panel_open() for display in widget._runtime.displays] == [False]

    def test_new_viewer_starts_closed_after_another_viewer_opened_tools(self, qtbot: QtBot) -> None:
        first = self._open_viewer(qtbot, _make_local_content("first", with_overlay=True))
        first.toggle_media_tools()

        second = self._open_viewer(qtbot, _make_local_content("second", with_overlay=True))
        assert second._runtime is not None
        assert [display.is_side_panel_open() for display in second._runtime.displays] == [False]
