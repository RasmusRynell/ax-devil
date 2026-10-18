"""Tests for the offline video viewer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QPointF, QPropertyAnimation
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
    QTabBar,
    QToolButton,
)
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.data_types import FrameData, FrameIdentifier, OverlayData
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.data_sources.base import SeekableFrameSource
from ax_devil.modules.data_sources.scene_history import FrameEvent, ObjectHistory, SceneHistory
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager, SceneRenderCatalogStore
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.draggable import DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer import (
    offline_entry_media,
    offline_video_viewer,
    offline_viewer_runtime,
    scene_frame_presenter,
)
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineLane, OfflineSession
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.catalog_helpers import GatedCatalogLoader
from tests.helpers.qt_events import make_wheel_event
from tests.helpers.workspace import store_with


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

    def wait(self, timeout_ms: int = 2000) -> bool:
        self.wait_timeouts.append(timeout_ms)
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


class _SourceSamples:
    """Analysis snapshot of a test overlay source: the fallback policy it had when taken, answered by that source."""

    def __init__(self, source: _SpecFileOverlaySource, policy: TimestampFallbackPolicy) -> None:
        self._source = source
        self._policy = policy

    def scene_history(
        self,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
        token: CancelToken,
    ) -> SceneHistory:
        return self._source.history_under(
            self._policy, video_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us
        )

    def analyze_alignment(
        self, _handler_type: str, video_timeline: FrameTimeline, token: CancelToken
    ) -> OverlayAlignmentReport:
        return self._source.report_under(self._policy, video_timeline)


class _SpecFileOverlaySource(_TrackedOverlaySource):
    """FileOverlaySource test double created from source specs and decoder lookup.

    Its history names the settings it was placed under, and its analyses fail with *failure* while that is set, so
    tests can tell which setting the displayed results follow.
    """

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
        self.failure: str | None = None
        if registry is not None:
            registry.append(self)

    @property
    def handler_type(self) -> str:
        return self._handler_type

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        return self._timestamp_fallback_policy

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        self._timestamp_fallback_policy = policy

    def samples(self) -> _SourceSamples:
        return _SourceSamples(self, self._timestamp_fallback_policy)

    def scene_history(
        self, video_timeline: FrameTimeline, *, allow_previous: bool, max_sample_age_us: int | None, token: CancelToken
    ) -> SceneHistory:
        return self.history_under(
            self._timestamp_fallback_policy,
            video_timeline,
            allow_previous=allow_previous,
            max_sample_age_us=max_sample_age_us,
        )

    def history_under(
        self,
        policy: TimestampFallbackPolicy,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
    ) -> SceneHistory:
        self.history_selections.append((allow_previous, max_sample_age_us))
        if self.failure is not None:
            raise RuntimeError(self.failure)

        def frame_id(index: int) -> FrameIdentifier:
            return FrameIdentifier(sequence_id=index, timestamp_us=video_timeline.timestamps_us[index])

        settings = ["no fallback"] if policy.effective_tolerance_us == 0 else []
        settings += [] if allow_previous else ["no sticky"]
        suffix = f" ({', '.join(settings)})" if settings else ""
        label = f"Delete {self.name}{suffix}"
        return SceneHistory(
            events=(FrameEvent(frame_id(20), "Delete", label, (self.name,)),),
            objects=(ObjectHistory(self.name, ("human",), ((10, 19),), video_timeline.timestamps_us),),
            frame_count=video_timeline.count,
        )

    def analyze_alignment(self, video_timeline: FrameTimeline, token: CancelToken) -> OverlayAlignmentReport:
        return self.report_under(self._timestamp_fallback_policy, video_timeline)

    def report_under(self, policy: TimestampFallbackPolicy, video_timeline: FrameTimeline) -> OverlayAlignmentReport:
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return OverlayAlignmentReport(
            handler_type=self.handler_type,
            total_video_frames=video_timeline.count,
            total_overlay_frames=0,
            exact_matches=0,
            max_abs_nearest_offset_us=None,
            sample_period_modes_us=(),
            tolerance_us=policy.effective_tolerance_us,
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
    """Assert the frame source was stopped, waited for, and scheduled for deletion exactly once."""

    assert source.stop_calls == 1
    assert len(source.wait_timeouts) == 1
    assert source.delete_later_calls == 1


def _assert_overlay_source_disposed(source: _TrackedOverlaySource) -> None:
    """Assert the overlay source was closed and scheduled for deletion exactly once."""

    assert source.close_calls == 1
    assert source.delete_later_calls == 1


def _frame_data(frame_number: int) -> FrameData:
    """Build a minimal frame payload for viewer runtime tests."""

    frame_id = FrameIdentifier(sequence_id=frame_number, timestamp_us=frame_number * 1000)
    return FrameData(content=QImage(2, 2, QImage.Format.Format_RGB32), frame_id=frame_id, source_id="video")


def _count_frame_viewports() -> int:
    """Count live frame viewports across the current Qt application."""

    return sum(isinstance(candidate, FrameViewport) for candidate in QApplication.allWidgets())


def _assert_no_stale_renderers(widget: OfflineVideoViewerWidget, baseline_frame_viewports: int) -> None:
    """Assert only the shown lanes' viewports exist and report render metrics."""

    live = len(widget.findChildren(FrameViewport))
    assert len(get_render_metrics_store().snapshot()) == live
    assert _count_frame_viewports() == baseline_frame_viewports + live


def _displays(widget: OfflineVideoViewerWidget) -> list[FrameDisplay]:
    """Return the lane displays of the shown entry, in lane order."""

    displays: list[FrameDisplay] = widget.findChildren(FrameDisplay)
    return displays


def _show_events(widget: OfflineVideoViewerWidget) -> None:
    """Show the viewer with its side panels open on their Events tab; the page lists events only while shown."""

    widget.show()
    for side_panel in widget.findChildren(DraggablePanel):
        side_panel.expand()
    for tabs in widget.findChildren(QTabBar, "mediaToolsTabs"):
        tabs.setCurrentIndex(1)


def _events_view(panel: MediaToolsPanel) -> RowTableView:
    view = panel.events_page.findChild(RowTableView)
    assert view is not None
    return view


def _event_texts(panel: MediaToolsPanel) -> list[str]:
    """Return the labels of the events the panel lists now."""

    model = _events_view(panel).rows_model
    return [cast(Any, model.row_at(row)).event.label for row in range(model.rowCount())]


def _alignment_text(timing: TimingDiagnosticsWidget) -> str:
    """Return the status text the timing widget shows now."""

    return "\n".join(label.text() for label in timing.findChildren(QLabel))


def _transports(widget: OfflineVideoViewerWidget) -> list[SeekableVideoControlPanel]:
    """Return the playback control bars the viewer shows."""

    panels: list[SeekableVideoControlPanel] = widget.findChildren(SeekableVideoControlPanel)
    return [panel for panel in panels if panel.isVisibleTo(widget)]


def _nav_button(widget: OfflineVideoViewerWidget, name: str) -> QPushButton:
    buttons: list[QPushButton] = widget.findChildren(QPushButton)
    return next(button for button in buttons if button.accessibleName() == name)


def _nav_label(widget: OfflineVideoViewerWidget) -> QLabel:
    labels: list[QLabel] = widget.findChildren(QLabel)
    return next(label for label in labels if label.text().startswith("Entry "))


def _shown_entry_index(widget: OfflineVideoViewerWidget) -> int:
    """Return the index of the entry the viewer reports as on screen."""
    item = widget.current_on_screen_item()
    assert item is not None
    return widget._content.bookmark_entries.index(item.entry)


def _session(widget: OfflineVideoViewerWidget) -> OfflineSession:
    """Return the shown entry's session, for lane state the viewer does not expose (presenters, overlay sources)."""

    runtime = widget._runtime  # noqa: SLF001
    assert runtime is not None
    return runtime


def _playlist(*names: str) -> PlaylistContent:
    return PlaylistContent(
        display_name="Test Playlist",
        entries=tuple(
            PlaylistEntry(lanes=_make_local_content(name).standalone_lanes(), default_considered=True) for name in names
        ),
    )


def _lane_entry(*videos: SeekableVideoContent) -> PlaylistEntry:
    """Build one entry comparing *videos* side by side."""
    return PlaylistEntry(
        lanes=tuple(
            EntryLane(display_name=video.display_name, video=video, default_considered=True) for video in videos
        ),
        default_considered=True,
    )


def _warning_text(widget: OfflineVideoViewerWidget) -> str:
    """Return the text of the one banner the viewer shows."""
    (banner,) = [banner for banner in widget.findChildren(Banner) if not banner.isHidden()]
    return " ".join(label.text() for label in banner.findChildren(QLabel))


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


class _DeferredBackground(BackgroundJobs):
    """Background jobs that run on the GUI thread, at once or when the test finishes them as a slow open would.

    Cancellation and delivery are the real rules; only the worker thread is replaced.
    """

    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self.immediate = True
        self.pending: list[Any] = []

    def _start(self, job: Any) -> None:
        self._unfinished.add(job)
        if self.immediate:
            self._complete(job)
        else:
            self.pending.append(job)

    def finish_next(self) -> None:
        """Complete the oldest pending job, skipping its work if it was cancelled, and deliver its outcome."""
        self._complete(self.pending.pop(0))

    def _complete(self, job: Any) -> None:
        self._run(job)
        self._finish(job)


@pytest.fixture(autouse=True)
def _background(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> _DeferredBackground:
    """Open entries as soon as they are requested, and run releases immediately."""
    background = _DeferredBackground()

    def release_now(media: EntryMedia) -> None:
        media.close()
        assert all(source.wait(0) for source in media.video_sources)
        media.delete_later()

    monkeypatch.setattr(offline_entry_media, "background_jobs", lambda *_args, **_kwargs: background)
    monkeypatch.setattr(offline_entry_media, "release_media", release_now)
    monkeypatch.setattr(offline_viewer_runtime, "release_media", release_now)
    monkeypatch.setattr(offline_video_viewer, "release_media", release_now)
    return background


@pytest.fixture
def deferred_background(_background: _DeferredBackground) -> _DeferredBackground:
    """Make entry opening wait for the test to finish it."""
    _background.immediate = False
    return _background


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

    def _open(
        self,
        qtbot: QtBot,
        content: SeekableVideoContent | PlaylistContent,
        workspace_store: WorkspaceStore | None = None,
    ) -> OfflineVideoViewerWidget:
        widget = OfflineVideoViewerWidget(
            content, consideration_query=workspace_store, render_catalog_manager=self._render_catalog_manager
        )
        _attach_offline_widget(qtbot, widget)
        return widget

    def test_construction_defers_initial_entry_load(self, qtbot: QtBot) -> None:
        opened: list[_TrackedFrameSource] = []
        content = _make_seekable_content("Solo", frame_source_opener=_make_tracked_frame_source_opener("Solo", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        assert widget.get_display_name() == "Solo"
        assert opened == []

        widget.on_workspace_attached()

        assert len(opened) == 1
        assert len(_displays(widget)) == 1

    def test_set_display_name_updates_the_name_and_the_header(self, qtbot: QtBot) -> None:
        content = _make_seekable_content("Solo")
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        widget.set_display_name("North gate")

        assert widget.get_display_name() == "North gate"
        assert widget._title_label.text() == "North gate"

    def test_consideration_changes_after_cleanup_open_nothing(self, qtbot: QtBot) -> None:
        """A closed viewer ignores workspace consideration updates instead of reopening entries."""
        opened: list[_TrackedFrameSource] = []
        videos = [
            _make_seekable_content(name, frame_source_opener=_make_tracked_frame_source_opener(name, opened))
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        workspace_store, playlist = store_with(playlist)
        widget = self._open(qtbot, playlist, workspace_store)
        assert len(opened) == 1

        widget.cleanup()
        entry_ref = playlist.entry_consideration_ref(0)
        workspace_store.set_item_considered(entry_ref, False)
        widget.refresh_item_consideration(entry_ref, False)
        lane_ref = playlist.lane_consideration_ref(0, 0)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(opened) == 1
        assert _displays(widget) == []

    def test_a_change_to_another_playlist_of_the_same_item_does_not_reopen_the_entry_on_screen(
        self, qtbot: QtBot
    ) -> None:
        opened: list[_TrackedFrameSource] = []
        video = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", opened))
        entries = (PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True),)
        workspace_store, playlist = store_with(PlaylistContent(display_name="Runs", entries=entries))
        widget = self._open(qtbot, playlist, workspace_store)
        other = PlaylistContent(display_name="Other", entries=entries, item_id=playlist.item_id)

        widget.refresh_item_consideration(other.lane_consideration_ref(0, 0), False)

        assert len(opened) == 1

    def test_playlist_navigation_buttons_step_between_entries(self, qtbot: QtBot) -> None:
        widget = self._open(qtbot, _playlist("A", "B"))
        previous, following = _nav_button(widget, "Previous entry"), _nav_button(widget, "Next entry")

        assert _shown_entry_index(widget) == 0
        assert _nav_label(widget).text() == "Entry 1 / 2"
        assert not previous.isEnabled() and following.isEnabled()

        following.click()

        assert _shown_entry_index(widget) == 1
        assert _nav_label(widget).text() == "Entry 2 / 2"
        assert previous.isEnabled() and not following.isEnabled()

        widget.step_next_entry()
        assert _shown_entry_index(widget) == 1

        previous.click()
        assert _shown_entry_index(widget) == 0

    def test_navigation_controls_position_stays_stable_while_loading(
        self, qtbot: QtBot, deferred_background: _DeferredBackground
    ) -> None:
        """Playlist navigation controls should not shift while an entry opens."""
        widget = self._open(qtbot, _playlist("A", "B"))
        widget.resize(800, 600)
        widget.show()
        deferred_background.finish_next()
        QCoreApplication.processEvents()
        nav_bar = _nav_label(widget).parentWidget()
        assert nav_bar is not None and nav_bar.isVisible()
        baseline = nav_bar.mapTo(widget, QPoint(0, 0))

        widget.step_next_entry()
        QCoreApplication.processEvents()
        assert nav_bar.isVisible()
        assert nav_bar.mapTo(widget, QPoint(0, 0)) == baseline

        deferred_background.finish_next()
        QCoreApplication.processEvents()
        assert nav_bar.mapTo(widget, QPoint(0, 0)) == baseline

    def test_playlist_navigation_skips_disabled_entries(self, qtbot: QtBot) -> None:
        playlist = _playlist("A", "B", "C")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(1), False)
        widget = self._open(qtbot, playlist, workspace_store)

        widget.step_next_entry()
        assert _shown_entry_index(widget) == 2

        widget.step_prev_entry()
        assert _shown_entry_index(widget) == 0

    def test_playlist_starts_on_first_enabled_entry(self, qtbot: QtBot) -> None:
        playlist = _playlist("A", "B")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(0), False)
        widget = self._open(qtbot, playlist, workspace_store)

        assert _shown_entry_index(widget) == 1

    def test_a_bookmark_opens_its_entry_at_its_frame_even_when_the_entry_is_excluded(self, qtbot: QtBot) -> None:
        """Opening a bookmark of an excluded entry shows that entry, not the next considered one."""
        playlist = _playlist("A", "B")
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.entry_consideration_ref(0), False)
        widget = OfflineVideoViewerWidget(
            playlist,
            consideration_query=workspace_store,
            render_catalog_manager=self._render_catalog_manager,
            start_index=0,
            start_frame=5,
        )
        _attach_offline_widget(qtbot, widget)

        assert _shown_entry_index(widget) == 0
        assert widget._runtime is not None and widget._runtime.current_frame == 5

    def test_source_paths_are_the_overlay_files_of_the_considered_lanes_on_screen(self, qtbot: QtBot) -> None:
        """A hidden lane's overlay file is not watched for the entry on screen, so rewriting it offers no reload."""
        video = _make_seekable_content("A", overlays=(_make_overlay_content("first"), _make_overlay_content("second")))
        workspace_store, video = store_with(video)
        widget = self._open(qtbot, video, workspace_store)
        assert widget.source_paths().files == {Path("/tmp/first.txt"), Path("/tmp/second.txt")}

        workspace_store.set_item_considered(video.lane_consideration_ref(0, 1), False)

        assert widget.source_paths().files == {Path("/tmp/first.txt")}

    def test_excluding_current_playlist_entry_navigates_to_next_considered(self, qtbot: QtBot) -> None:
        playlist = _playlist("A", "B")
        workspace_store, playlist = store_with(playlist)
        widget = self._open(qtbot, playlist, workspace_store)
        assert _shown_entry_index(widget) == 0

        entry_ref = playlist.entry_consideration_ref(0)
        workspace_store.set_item_considered(entry_ref, False)
        widget.refresh_item_consideration(entry_ref, False)

        assert _shown_entry_index(widget) == 1

    def test_single_video_pane_names_it_once_and_describes_it_in_the_header(self, qtbot: QtBot) -> None:
        """A lane named like its pane shows no name on the video; the header shows the video's size, rate and length."""
        source = _stub_frame_source()
        source.get_duration_s.return_value = 4.0
        widget = self._open(qtbot, _make_seekable_content("Solo", frame_source_opener=lambda: source))
        widget.resize(900, 600)
        widget.show()

        assert widget.findChild(QLabel, "lane-indicator-label") is None
        assert widget.header_details() == "640×480 · 25 fps · 0:04"
        (controls,) = _transports(widget)
        assert controls.timeline_slider.cached_ranges() == ((0, 9),)

    def test_closing_the_viewer_stops_transport_fading(self, qtbot: QtBot) -> None:
        """A closed playlist viewer's shared transport stops fading, so no animation or timer outlives its widgets."""
        videos = [_make_seekable_content(name) for name in ("A", "B")]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        widget = self._open(qtbot, playlist)
        (controls,) = _transports(widget)
        controls.start_auto_hide_timer()
        controls.fade_out()
        assert controls.hide_timer.isActive()
        assert controls.fade_animation.state() == QPropertyAnimation.State.Running

        widget.cleanup()
        controls.fade_in()
        controls.start_auto_hide_timer()

        assert not controls.hide_timer.isActive()
        assert controls.fade_animation.state() == QPropertyAnimation.State.Stopped

    def test_single_video_controls_drive_playback(self, qtbot: QtBot) -> None:
        source = _stub_frame_source()
        widget = self._open(qtbot, _make_seekable_content("Solo", frame_source_opener=lambda: source))
        (controls,) = widget.findChildren(SeekableVideoControlPanel)

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

    def test_navigation_failure_warns_and_stays_on_failed_entry(self, qtbot: QtBot) -> None:
        """A failed entry leaves the viewer empty on it, with its opened sources released, and stepping still works."""
        opened: list[_TrackedFrameSource] = []

        def broken_open() -> object:
            raise RuntimeError("broken entry")

        v1 = _make_local_content("A")
        v2 = _make_seekable_content("B", frame_source_opener=_make_tracked_frame_source_opener("B", opened))
        v3 = _make_seekable_content("C", frame_source_opener=broken_open)
        entry_with_failure = PlaylistEntry(
            lanes=(*v2.standalone_lanes(), *v3.standalone_lanes()),
            default_considered=True,
        )
        playlist = PlaylistContent(
            display_name="Test",
            entries=(PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True), entry_with_failure),
        )
        widget = self._open(qtbot, playlist)

        widget.step_next_entry()

        assert _shown_entry_index(widget) == 1
        assert _displays(widget) == []
        assert "broken entry" in _warning_text(widget)
        assert len(opened) == 1
        _assert_frame_source_disposed(opened[0])

        widget.step_prev_entry()

        assert _shown_entry_index(widget) == 0
        assert len(_displays(widget)) == 1

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
        widget = self._open(qtbot, playlist)
        deferred_background.finish_next()
        assert len(_displays(widget)) == 1

        widget.step_next_entry()
        assert _displays(widget) == []
        _assert_frame_source_disposed(sources["A"][0])
        widget.step_next_entry()
        assert _shown_entry_index(widget) == 2
        assert _nav_label(widget).text() == "Entry 3 / 3"

        deferred_background.finish_next()
        assert _displays(widget) == []
        assert sources["B"] == []

        deferred_background.finish_next()
        assert len(_displays(widget)) == 1
        assert len(sources["C"]) == 1
        assert sources["C"][0].stop_calls == 0

    def test_cleanup_while_loading_never_installs_the_entry(
        self, qtbot: QtBot, deferred_background: _DeferredBackground
    ) -> None:
        """Closing the viewer mid-open does not wait for the open, and the entry is never shown or left open."""
        opened: list[_TrackedFrameSource] = []
        content = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", opened))
        widget = self._open(qtbot, content)

        widget.cleanup()
        deferred_background.finish_next()

        assert _displays(widget) == []
        assert all(source.stop_calls == 1 for source in opened)

    def test_display_build_failure_warns_and_releases_the_entry(self, qtbot: QtBot) -> None:
        """A failure while building an entry's displays is reported like a failed open, with its sources released."""
        opened: list[_TrackedFrameSource] = []
        content = _make_seekable_content("A", frame_source_opener=_make_tracked_frame_source_opener("A", opened))
        widget = OfflineVideoViewerWidget(content, render_catalog_manager=self._render_catalog_manager)
        qtbot.addWidget(widget)

        with patch.object(OfflineSession, "build", side_effect=RuntimeError("broken display")):
            widget.on_workspace_attached()

        assert _displays(widget) == []
        assert "broken display" in _warning_text(widget)
        assert len(opened) == 1
        _assert_frame_source_disposed(opened[0])

    def test_shared_video_multi_overlay_lanes_follow_one_timeline(self, qtbot: QtBot) -> None:
        """Lanes comparing overlays on one video share its source and one transport, and look up the shown frame."""
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
        widget = self._open(qtbot, content)

        assert len(frame_sources) == 1
        assert len(overlay_sources) == 2
        assert len(_displays(widget)) == 2
        (controls,) = widget.findChildren(SeekableVideoControlPanel)
        assert not controls.isHidden()
        assert controls.timeline_slider.maximum() == 99
        (source,) = frame_sources

        def shown_frames() -> tuple[int, list[int]]:
            return controls.timeline_slider.value(), [
                overlay.requested_frame_ids[-1].sequence_id for overlay in overlay_sources
            ]

        source.frameReady.emit(_frame_data(7))
        QCoreApplication.processEvents()
        assert shown_frames() == (7, [7, 7])

        controls.jumpToRequested.emit(12)
        assert source.get_current_frame() == 12
        source.frameReady.emit(_frame_data(12))
        QCoreApplication.processEvents()
        assert shown_frames() == (12, [12, 12])

        widget.step_frames(3)
        assert source.get_current_frame() == 15

        controls.set_playback_speed(1.1)
        assert source.speed_updates[-1] == 1.1
        widget.set_playback_speed(1.7)
        assert source.speed_updates[-1] == 1.7

    def test_multi_video_lanes_follow_primary_frame_index(self, qtbot: QtBot) -> None:
        """Distinct video lanes should asynchronously render the primary timeline frame."""
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []
        videos = [
            _make_seekable_content(
                name,
                frame_source_opener=_make_tracked_frame_source_opener(name, frame_sources),
                overlays=(_make_overlay_content(f"overlay-{name}", overlay_sources),),
            )
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=tuple(
                        EntryLane(
                            display_name=video.display_name,
                            video=video,
                            overlay=video.overlays[0],
                            default_considered=True,
                        )
                        for video in videos
                    ),
                    default_considered=True,
                ),
            ),
        )
        widget = self._open(qtbot, playlist)

        assert len(_displays(widget)) == 2
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2

        frame_sources[0].frameReady.emit(_frame_data(7))
        QCoreApplication.processEvents()

        assert frame_sources[1].async_frame_requests[-1] == 7
        assert [source.requested_frame_ids[-1].sequence_id for source in overlay_sources] == [7, 7]

    def test_lane_history_follows_playback_and_jumps_to_requested_frames(self, qtbot: QtBot) -> None:
        """Each lane lists its overlay's history, follows the displayed frame and seeks on request."""
        frame_sources: list[_TrackedFrameSource] = []
        content = _make_seekable_content(
            "events",
            frame_source_opener=_make_tracked_frame_source_opener("video", frame_sources),
            overlays=(_make_overlay_content("first"), _make_overlay_content("second")),
        )
        widget = self._open(qtbot, content)
        panels = widget.findChildren(MediaToolsPanel)
        _show_events(widget)
        event_pages = [panel.events_page for panel in panels]
        assert all(page.isVisible() for page in event_pages)
        assert [_event_texts(panel) for panel in panels] == [["Delete first"], ["Delete second"]]

        panels[1].frameRequested.emit(20)
        assert frame_sources[0].get_current_frame() == 20
        frame_sources[0].frameReady.emit(_frame_data(20))
        QCoreApplication.processEvents()
        qtbot.waitUntil(lambda: [cast(Any, _events_view(panel)).position for panel in panels] == [(0, 1), (0, 1)])

        panels[0].frameRequested.emit(12)
        assert frame_sources[0].get_current_frame() == 12

    def test_lane_history_follows_sticky_and_fallback_settings(self, qtbot: QtBot) -> None:
        """Each sticky or fallback change shows a history and alignment report made under the new setting."""
        overlay_sources: list[_TrackedOverlaySource] = []
        content = _make_seekable_content("history", overlays=(_make_overlay_content("only", overlay_sources),))
        widget = self._open(qtbot, content)
        (panel,) = widget.findChildren(MediaToolsPanel)
        (timing,) = widget.findChildren(TimingDiagnosticsWidget)
        (source,) = overlay_sources
        assert isinstance(source, _SpecFileOverlaySource)
        _show_events(widget)
        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=True, timeout_ms=1500))
        qtbot.waitUntil(lambda: source.history_selections[-1] == (True, 1_500_000), timeout=5000)

        def shows(history: str, tolerance: str) -> Callable[[], bool]:
            def shown() -> bool:
                alignment = _alignment_text(timing)
                return _event_texts(panel) == [history] and f"tol {tolerance}" in alignment

            return shown

        qtbot.waitUntil(shows("Delete only", "50 ms"), timeout=5000)

        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=False))
        qtbot.waitUntil(lambda: _event_texts(panel) == ["Delete only (no sticky)"], timeout=5000)
        assert source.history_selections[-1] == (False, None)

        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=0))
        qtbot.waitUntil(shows("Delete only (no fallback, no sticky)", "0 ms"), timeout=5000)

        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=True))
        qtbot.waitUntil(shows("Delete only (no fallback)", "0 ms"), timeout=5000)

    def test_failed_lane_analysis_is_shown_and_the_next_setting_change_recovers(self, qtbot: QtBot) -> None:
        """A history or alignment analysis that fails says so on its lane, keeps the old results and can be retried."""
        overlay_sources: list[_TrackedOverlaySource] = []
        content = _make_seekable_content("history", overlays=(_make_overlay_content("only", overlay_sources),))
        widget = self._open(qtbot, content)
        (panel,) = widget.findChildren(MediaToolsPanel)
        (timing,) = widget.findChildren(TimingDiagnosticsWidget)
        (indicator,) = widget.findChildren(OverlayAlignmentIndicator)
        (source,) = overlay_sources
        assert isinstance(source, _SpecFileOverlaySource)
        notice = panel.findChild(QLabel, "historyOutOfDateNotice")
        assert notice is not None
        _show_events(widget)
        qtbot.waitUntil(lambda: "tol 50 ms" in _alignment_text(timing), timeout=5000)
        assert notice.isHidden()

        source.failure = "frame index unreadable"
        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=0))

        qtbot.waitUntil(lambda: not notice.isHidden() and not indicator.isHidden(), timeout=5000)
        for text in (notice.text(), _alignment_text(timing), indicator.toolTip()):
            assert "lane '" in text
            assert "frame index unreadable" in text
        assert _event_texts(panel) == ["Delete only"]
        assert "tol 50 ms" in _alignment_text(timing)

        source.failure = None
        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=10_000))

        qtbot.waitUntil(lambda: notice.isHidden() and "tol 10 ms" in _alignment_text(timing), timeout=5000)
        assert _event_texts(panel) == ["Delete only"]
        assert "frame index unreadable" not in _alignment_text(timing)
        assert indicator.isHidden()

    @pytest.mark.parametrize("lane_count, columns", [(2, 2), (3, 2), (5, 3)])
    def test_lane_geometry(self, qtbot: QtBot, lane_count: int, columns: int) -> None:
        """Lanes fill equal visible cells, wrap into rows without overlap and keep usable widths when shrunk."""
        playlist = PlaylistContent(
            display_name="comparison",
            entries=(_lane_entry(*(_make_local_content(f"Video {index}") for index in range(lane_count))),),
        )
        widget = self._open(qtbot, playlist)
        widget.show()
        displays = _displays(widget)
        assert len(displays) == lane_count
        panes = [display.parentWidget() for display in displays]
        container = panes[0].parentWidget() if panes[0] is not None else None
        assert container is not None and all(pane is not None for pane in panes)
        for width in (1200, 400):
            widget.resize(width, 700)
            QApplication.processEvents()
            rects = [pane.geometry() for pane in panes if pane is not None]
            assert all(display.isVisible() for display in displays)
            assert max(rect.width() for rect in rects) - min(rect.width() for rect in rects) <= 1
            assert all(container.rect().contains(rect) for rect in rects)
            assert all(display.width() >= display.minimumSizeHint().width() for display in displays)
            for index, rect in enumerate(rects):
                assert rect.y() == rects[index // columns * columns].y()
                if index % columns:
                    assert rect.x() > rects[index - 1].right()
                if index >= columns:
                    assert rect.y() > rects[index - columns].bottom()

    def test_entry_without_considered_lanes_shows_placeholder_and_clears_the_shared_controls(
        self, qtbot: QtBot
    ) -> None:
        """Stepping to an entry whose lanes are all excluded leaves no frames, time or cache from the previous one."""
        playlist = PlaylistContent(
            display_name="Playlist",
            entries=(
                PlaylistEntry(
                    lanes=_make_local_content("A", with_overlay=True).standalone_lanes(), default_considered=True
                ),
                PlaylistEntry(
                    lanes=_make_local_content("B", with_overlay=True).standalone_lanes(), default_considered=True
                ),
            ),
        )
        workspace_store, playlist = store_with(playlist)
        workspace_store.set_item_considered(playlist.lane_consideration_ref(1, 0), False)
        widget = self._open(qtbot, playlist, workspace_store)
        widget.show()
        (controls,) = _transports(widget)
        assert controls.timeline_slider.maximum() > 0

        widget.step_next_entry()
        QCoreApplication.processEvents()

        assert _displays(widget) == []
        placeholder = [
            label for label in widget.findChildren(QLabel) if label.text() == "No considered lanes in this entry"
        ]
        assert len(placeholder) == 1 and placeholder[0].isVisible()
        assert controls.timeline_slider.maximum() == 0
        assert controls.timeline_slider.cached_ranges() == ()
        time = controls.timecode_text()
        assert time == "" or not any(
            label.isVisibleTo(controls) for label in controls.findChildren(QLabel) if label.text() == time
        )
        assert widget.header_details() == ""

    def test_multi_lane_entries_share_one_transport_over_the_viewer(self, qtbot: QtBot) -> None:
        """Every playlist entry, one lane or several, gets one shared transport over the bottom of the video."""
        v1 = _make_local_content("A")
        v2 = _make_local_content("B")
        playlist = PlaylistContent(
            display_name="mixed",
            entries=(
                PlaylistEntry(lanes=v1.standalone_lanes(), default_considered=True),
                _lane_entry(v1, v2),
                PlaylistEntry(lanes=v2.standalone_lanes(), default_considered=True),
            ),
        )
        widget = self._open(qtbot, playlist)
        widget.resize(800, 600)
        widget.show()

        for entry_index, lane_count in enumerate((1, 2, 1)):
            assert _shown_entry_index(widget) == entry_index
            QCoreApplication.processEvents()
            displays = _displays(widget)
            assert len(displays) == lane_count
            assert len(widget.findChildren(SeekableVideoControlPanel)) == 1
            (transport,) = _transports(widget)
            transport_rect = transport.rect().translated(transport.mapTo(widget, QPoint(0, 0)))
            video_bottom = max(
                display.rect().translated(display.mapTo(widget, QPoint(0, 0))).bottom() for display in displays
            )
            assert widget.rect().contains(transport_rect)
            assert transport_rect.bottom() == video_bottom
            widget.step_next_entry()

    def test_keyboard_zoom_preserves_fullscreen_lane_center(self, qtbot: QtBot) -> None:
        """Zoom uses the focused lane's pan even when a smaller peer has clamped it away."""
        playlist = PlaylistContent(
            display_name="multi", entries=(_lane_entry(_make_local_content("A"), _make_local_content("B")),)
        )
        widget = self._open(qtbot, playlist)
        widget.resize(700, 1000)
        widget.show()
        widget.activateWindow()
        qtbot.waitUntil(widget.isActiveWindow)
        first, second = _displays(widget)
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0)
        frame = VideoFrameWithOverlays(VideoFrame(image, 0, 0), None)
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
                second.viewport.wheelEvent(make_wheel_event(anchor, 120))
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

    def test_lane_names_follow_visible_video_and_hide_for_info_or_when_turned_off(self, qtbot: QtBot) -> None:
        """Lane names sit in the visible video's top-left corner; the info overlay or the preference hides them."""
        playlist = PlaylistContent(
            display_name="multi", entries=(_lane_entry(_make_local_content("A"), _make_local_content("B")),)
        )
        widget = self._open(qtbot, playlist)
        displays = _displays(widget)
        names = [display.viewport.findChild(QLabel, "lane-indicator-label") for display in displays]
        assert [name.text() for name in names if name is not None] == ["A", "B"]

        def shown() -> list[bool]:
            return [name is not None and not name.isHidden() for name in names]

        widget.toggle_info_overlay()
        assert shown() == [False, False]
        widget.toggle_info_overlay()
        assert shown() == [True, True]

        widget.resize(700, 1000)
        widget.show()
        qtbot.waitExposed(widget)
        first_viewport = displays[0].viewport
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.fill(0)
        first_viewport.display_frame(VideoFrameWithOverlays(VideoFrame(image=image, timestamp_us=0), overlays=None))
        first_name = names[0]
        assert first_name is not None
        bars = first_viewport.visible_frame_rect()
        assert bars is not None and bars.top() > 0
        assert first_name.pos() == QPoint(round(bars.left()) + 10, round(bars.top()) + 10)
        fill_zoom = first_viewport.height() / bars.height()
        while first_viewport.viewport_state.zoom_level < fill_zoom:
            first_viewport.wheelEvent(make_wheel_event(QPointF(100, 80), 120))
        assert first_viewport.viewport_state.zoom_level > 1.0
        assert shown() == [True, True]
        assert first_name.pos() == QPoint(10, 10)

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

    def test_lane_toggles_rebuild_compared_videos_and_release_old_sources(self, qtbot: QtBot) -> None:
        """Excluding and restoring a lane rebuilds the entry, releasing every replaced source and renderer."""
        get_render_metrics_store().clear()
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []
        videos = [
            _make_seekable_content(
                name,
                frame_source_opener=_make_tracked_frame_source_opener(name, frame_sources),
                overlays=(_make_overlay_content(f"overlay-{name}", overlay_sources),),
            )
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="multi",
            entries=(
                PlaylistEntry(
                    lanes=tuple(
                        EntryLane(
                            display_name=video.display_name,
                            video=video,
                            overlay=video.overlays[0],
                            default_considered=True,
                        )
                        for video in videos
                    ),
                    default_considered=True,
                ),
            ),
        )
        workspace_store, playlist = store_with(playlist)
        baseline_frame_viewports = _count_frame_viewports()
        widget = self._open(qtbot, playlist, workspace_store)
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 2
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        lane_ref = playlist.lane_consideration_ref(0, 1)
        workspace_store.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)

        assert len(_displays(widget)) == 1
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 3
        for source in frame_sources[:2]:
            _assert_frame_source_disposed(source)
        for overlay in overlay_sources[:2]:
            _assert_overlay_source_disposed(overlay)
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        workspace_store.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(_displays(widget)) == 2
        assert len(frame_sources) == 5
        assert len(overlay_sources) == 5
        _assert_frame_source_disposed(frame_sources[2])
        _assert_overlay_source_disposed(overlay_sources[2])
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert get_render_metrics_store().snapshot() == ()
        assert _count_frame_viewports() == baseline_frame_viewports

    def test_lane_toggles_rebuild_shared_video_lanes_and_release_old_sources(self, qtbot: QtBot) -> None:
        """Lanes comparing overlays on one video keep sharing one source across rebuilds and release the old ones."""
        get_render_metrics_store().clear()
        frame_sources: list[_TrackedFrameSource] = []
        overlay_sources: list[_TrackedOverlaySource] = []
        content = _make_seekable_content(
            "multi-overlay",
            frame_source_opener=_make_tracked_frame_source_opener("video", frame_sources),
            overlays=tuple(_make_overlay_content(name, overlay_sources) for name in ("first", "second", "third")),
        )
        workspace_store, content = store_with(content)
        baseline_frame_viewports = _count_frame_viewports()
        widget = self._open(qtbot, content, workspace_store)
        assert len(frame_sources) == 1
        assert len(overlay_sources) == 3
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        lane_ref = content.lane_consideration_ref(0, 1)
        workspace_store.set_item_considered(lane_ref, False)
        widget.refresh_item_consideration(lane_ref, False)

        assert len(_displays(widget)) == 2
        assert len(frame_sources) == 2
        assert len(overlay_sources) == 5
        _assert_frame_source_disposed(frame_sources[0])
        for overlay in overlay_sources[:3]:
            _assert_overlay_source_disposed(overlay)
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        workspace_store.set_item_considered(lane_ref, True)
        widget.refresh_item_consideration(lane_ref, True)

        assert len(_displays(widget)) == 3
        assert len(frame_sources) == 3
        assert len(overlay_sources) == 8
        _assert_frame_source_disposed(frame_sources[1])
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert get_render_metrics_store().snapshot() == ()
        assert _count_frame_viewports() == baseline_frame_viewports

    def test_entry_navigation_leaves_no_stale_renderers(self, qtbot: QtBot) -> None:
        """Offline entry rebuilds should keep renderers and their metrics scoped to the shown lanes."""
        get_render_metrics_store().clear()
        first = _make_local_content("A", with_overlay=True)
        second = _make_seekable_content("B", overlays=(_make_overlay_content("o1"), _make_overlay_content("o2")))
        playlist = PlaylistContent(
            display_name="playlist",
            entries=(
                PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
            ),
        )
        baseline_frame_viewports = _count_frame_viewports()
        widget = self._open(qtbot, playlist)
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.step_next_entry()
        assert len(_displays(widget)) == 2
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.step_prev_entry()
        _assert_no_stale_renderers(widget, baseline_frame_viewports)

        widget.cleanup()
        assert get_render_metrics_store().snapshot() == ()
        assert _count_frame_viewports() == baseline_frame_viewports

    def test_selected_playback_speed_is_reapplied_after_entry_switch(self, qtbot: QtBot) -> None:
        frame_sources: list[_TrackedFrameSource] = []
        videos = [
            _make_seekable_content(name, frame_source_opener=_make_tracked_frame_source_opener(name, frame_sources))
            for name in ("A", "B")
        ]
        playlist = PlaylistContent(
            display_name="Test Playlist",
            entries=tuple(PlaylistEntry(lanes=video.standalone_lanes(), default_considered=True) for video in videos),
        )
        widget = self._open(qtbot, playlist)

        widget.set_playback_speed(0.2)
        assert frame_sources[0].speed_updates[-1] == 0.2
        widget.step_next_entry()

        assert frame_sources[-1].speed_updates[-1] == 0.2

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
        widget = self._open(qtbot, playlist)

        def details(lane: OfflineLane) -> tuple[QCheckBox, QPushButton]:
            assert lane.tools_panel is not None
            confidence = lane.tools_panel.findChild(QCheckBox, "overlayFeature_confidence")
            reset = lane.tools_panel.findChild(QPushButton, "overlayVisibilityReset")
            assert confidence is not None and reset is not None
            return confidence, reset

        lane = _session(widget).lanes[0]
        assert lane.presenter is not None
        full = lane.presenter.scene_render_catalog
        confidence, _reset = details(lane)
        widget.pause_playback()
        with patch.object(lane.display, "refresh_overlays", wraps=lane.display.refresh_overlays) as refresh:
            confidence.setChecked(False)
            refresh.assert_called_once()
        assert not _session(widget).is_playing
        assert lane.presenter.scene_render_catalog is not full
        widget.step_next_entry()
        lane = _session(widget).lanes[0]
        confidence, reset = details(lane)
        assert not confidence.isChecked()
        assert lane.presenter is not None
        frozen = lane.presenter.scene_render_catalog
        with patch("ax_devil.modules.video_viewer.export.ExportDialog") as dialog_type:
            dialog_type.return_value.__enter__.return_value.exec.side_effect = reset.click
            widget.export_video()
            export_lanes = dialog_type.call_args.args[0]
        assert export_lanes[0].presenter.scene_render_catalog is frozen
        assert lane.presenter.scene_render_catalog is not frozen
        widget.step_prev_entry()
        confidence, _reset = details(_session(widget).lanes[0])
        assert confidence.isChecked()

    def test_export_waits_for_the_first_render_catalog_and_then_freezes_it(
        self, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """While a viewer's first catalog loads, nothing can be exported with the built-in one in its place."""

        def forbidden() -> None:
            raise AssertionError("Export fell back to the built-in catalog")

        monkeypatch.setattr(scene_frame_presenter, "get_built_in_scene_render_catalog", forbidden)
        store = SceneRenderCatalogStore(tmp_path / "render_catalogs")
        loader = GatedCatalogLoader()
        manager = SceneRenderCatalogManager(catalog_store=store, catalog_loader=loader)
        release = loader.hold(manager.default_catalog_path())
        manager.initialize()
        try:
            qtbot.waitUntil(lambda: manager.listing_ready, timeout=10_000)
            widget = OfflineVideoViewerWidget(
                _make_local_content("cold", with_overlay=True), render_catalog_manager=manager
            )
            _attach_offline_widget(qtbot, widget)
            lane = _session(widget).lanes[0]
            assert lane.tools_panel is not None and lane.presenter is not None
            more_button = lane.tools_panel.findChild(QToolButton, "mediaToolsMoreButton")
            assert more_button is not None and more_button.menu() is not None
            assert lane.export_action in more_button.menu().actions()
            assert lane.presenter.scene_render_catalog is None and not lane.export_action.isEnabled()

            with (
                patch("ax_devil.modules.video_viewer.export.ExportDialog") as dialog_type,
                patch("ax_devil.modules.video_viewer.offline_viewer_runtime.QMessageBox.warning") as told,
            ):
                widget.export_video()
                told.assert_called()
                assert "still loading" in told.call_args.args[2]
                told.reset_mock()
                _session(widget).export_lanes([lane])
                told.assert_called()
                assert "still loading" in told.call_args.args[2]
                dialog_type.assert_not_called()

                release.set()
                qtbot.waitUntil(lane.export_action.isEnabled, timeout=10_000)
                widget.export_video()
                exported = dialog_type.call_args.args[0]

            assert lane.presenter.scene_render_catalog is not None
            assert exported[0].presenter.scene_render_catalog is lane.presenter.scene_render_catalog
        finally:
            release.set()


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
        displays = _displays(widget)

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
        widget = self._open_viewer(qtbot, _playlist("A", "B"))
        widget.toggle_media_tools()
        assert [display.is_side_panel_open() for display in _displays(widget)] == [True]

        widget.step_next_entry()

        assert _shown_entry_index(widget) == 1
        assert [display.is_side_panel_open() for display in _displays(widget)] == [False]

    def test_new_viewer_starts_closed_after_another_viewer_opened_tools(self, qtbot: QtBot) -> None:
        first = self._open_viewer(qtbot, _make_local_content("first", with_overlay=True))
        first.toggle_media_tools()

        second = self._open_viewer(qtbot, _make_local_content("second", with_overlay=True))
        assert [display.is_side_panel_open() for display in _displays(second)] == [False]
