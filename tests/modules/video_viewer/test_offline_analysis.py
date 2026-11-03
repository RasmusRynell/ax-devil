"""Tests for the background analyses of an offline session's lanes: history and overlay alignment."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar, cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.data_sources.overlay_samples import OverlaySamples
from ax_devil.modules.data_sources.scene_history import SampleTrack, SceneHistory, SceneHistoryRecords
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentReport
from ax_devil.modules.filtering.filter_config import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Entity, EntityId
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackMode,
    TimestampFallbackPolicy,
    find_matching_overlay_timestamp,
)
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.page_stack import ObjectPageKey
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget
from tests.helpers.offline_session import (
    FakeDisplay,
    FakeOverlaySource,
    PlaybackSource,
    make_frame,
    make_lane,
    make_overlay,
    make_session,
)


class _Panel:
    """Tools panel test double recording the histories it shows and the notices that they are out of date."""

    def __init__(self) -> None:
        self.histories: list[SceneHistory] = []
        self.notices: list[str] = []

    def update_scene(self, *args: object, **kwargs: object) -> None:
        """Accept inspection of the held frame."""

    def refresh_scene(self) -> None:
        """Match MediaToolsPanel's immediate inspection refresh."""

    def set_scene_history(self, history: SceneHistory) -> None:
        """Record a shown history."""
        self.histories.append(history)

    def mark_history_out_of_date(self, reason: str) -> None:
        """Record a notice."""
        self.notices.append(reason)

    def cleanup(self) -> None:
        """Match MediaToolsPanel cleanup."""


class _AlignmentView:
    """Timing or indicator test double recording the alignment reports it shows and the notices that they are old."""

    def __init__(self) -> None:
        self.reports: list[OverlayAlignmentReport | None] = []
        self.notices: list[str] = []

    def update_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        """Record a shown report."""
        self.reports.append(report)

    def mark_alignment_out_of_date(self, reason: str) -> None:
        """Record a notice."""
        self.notices.append(reason)


_T = TypeVar("_T")


class _HeldSamples:
    """Real overlay samples whose analysis can be held back or made to fail, and that notice stopping early."""

    def __init__(
        self, samples: OverlaySamples, hold: threading.Event, history_failure: str | None, alignment_failure: str | None
    ) -> None:
        self._samples = samples
        self._hold = hold
        self._history_failure = history_failure
        self._alignment_failure = alignment_failure
        self.entered = threading.Event()
        self.stopped_early = False

    def _run_analysis(self, analysis: Callable[[], _T], failure: str | None) -> _T:
        self.entered.set()
        assert self._hold.wait(5)
        if failure is not None:
            raise RuntimeError(failure)
        try:
            return analysis()
        except Cancelled:
            self.stopped_early = True
            raise

    def scene_history(
        self,
        video_timeline: FrameTimeline,
        *,
        allow_previous: bool,
        max_sample_age_us: int | None,
        token: CancelToken,
    ) -> SceneHistory:
        """Place the real history once released."""
        return self._run_analysis(
            lambda: self._samples.scene_history(
                video_timeline, allow_previous=allow_previous, max_sample_age_us=max_sample_age_us, token=token
            ),
            self._history_failure,
        )

    def analyze_alignment(
        self, handler_type: str, video_timeline: FrameTimeline, token: CancelToken
    ) -> OverlayAlignmentReport:
        """Analyze the real alignment once released."""
        return self._run_analysis(
            lambda: self._samples.analyze_alignment(handler_type, video_timeline, token),
            self._alignment_failure,
        )


class _SampledOverlaySource:
    """Overlay source test double handing out snapshots of real samples under its current policy."""

    handler_type = "TEST"

    def __init__(self, samples: OverlaySamples) -> None:
        self._samples = samples
        self.hold = threading.Event()  # Analyses started from a snapshot wait for this until it is set.
        self.hold.set()
        self.failure: str | None = None  # Fails the analyses started from later snapshots.
        self.failure_spares_history = False
        self.snapshots: list[_HeldSamples] = []

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Apply *policy* to later snapshots."""
        self._samples = replace(self._samples, policy=policy)

    def samples(self) -> _HeldSamples:
        """Return a snapshot that holds or fails as the source is set to now."""
        history_failure = None if self.failure_spares_history else self.failure
        snapshot = _HeldSamples(self._samples, self.hold, history_failure, self.failure)
        self.snapshots.append(snapshot)
        return snapshot

    def get_overlay_at_frame(self, frame_id: FrameIdentifier, *, allow_previous: bool = False) -> OverlayData | None:
        """Look up a sample using the same timestamp rules as history placement."""
        match = find_matching_overlay_timestamp(
            sorted_overlay_timestamps_us=self._samples.timestamps_us,
            video_timestamp_us=frame_id.timestamp_us,
            policy=self._samples.policy,
            allow_previous=allow_previous,
        )
        if match.overlay_timestamp_us is None:
            return None
        return replace(
            make_overlay(1),
            frame_id=FrameIdentifier(1, match.overlay_timestamp_us),
            metadata={"timestamp_match_type": match.match_type},
        )


class _AnalysisRig:
    """One session with one lane over `_SampledOverlaySource`: samples at 0 and 20 ms, frames at 0, 40, 80, 120 ms."""

    def __init__(self, qtbot: QtBot, persistence: OverlayPersistenceSettings | None = None) -> None:
        self.display = FakeDisplay()
        self.overlay = _SampledOverlaySource(
            OverlaySamples(
                sequence_lookup=False,
                timestamps_us=(0, 20_000),
                sequences=(),
                sequence_to_timestamp={},
                timestamp_to_sequence={0: 0, 20_000: 1},
                records=SceneHistoryRecords(events=(), tracks=(SampleTrack("a", ("human",), ((0, 20_000),)),)),
                policy=TimestampFallbackPolicy(),
            )
        )
        self.panel = _Panel()
        self.timing = _AlignmentView()
        self.indicator = _AlignmentView()
        self.persistence = OverlayPersistencePolicy(persistence or OverlayPersistenceSettings.default_enabled())
        self.jobs = BackgroundJobs()
        self.lane = make_lane(
            self.display,
            overlay_source=cast(FakeOverlaySource, self.overlay),
            video_source=PlaybackSource(),
            overlay_policy=self.persistence,
            tools_panel=cast(MediaToolsPanel, self.panel),
            analysis_jobs=self.jobs.scope(self.display),
            timing_controls=cast(TimingDiagnosticsWidget, self.timing),
            alignment_indicator=cast(OverlayAlignmentIndicator, self.indicator),
        )
        self._finished = threading.Semaphore(0)
        self.jobs.finished.connect(
            lambda _job, finished=self._finished: finished.release(), Qt.ConnectionType.DirectConnection
        )
        self.session = make_session(qtbot, [self.lane], [self.lane.video_source], jobs=self.jobs)

    def wait_until_analyses_run(self, qtbot: QtBot) -> None:
        """Wait until every analysis requested so far has started."""
        snapshots = self.overlay.snapshots  # Not the rig itself, which the wait would keep alive.
        qtbot.waitUntil(lambda: all(snapshot.entered.is_set() for snapshot in snapshots), timeout=5000)

    def wait_for_analyses(self, count: int) -> None:
        """Wait until *count* more analyses finished, whether wanted or not, and let the GUI thread take them."""
        for _ in range(count):
            assert self._finished.acquire(timeout=5)
        QCoreApplication.processEvents()

    def shown_spans(self) -> list[tuple[str, tuple[tuple[int, int], ...]]]:
        """Return where each object appears in the history the panel shows now."""
        return [(history.entity_id, history.spans) for history in self.panel.histories[-1].objects]


def test_policy_change_keeps_the_viewer_responsive_while_analysis_runs(qtbot: QtBot) -> None:
    """A slow whole-file analysis never stops the session from taking commands; its result arrives when it finishes."""
    rig = _AnalysisRig(qtbot, OverlayPersistenceSettings(enabled=False))
    rig.overlay.hold.clear()

    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY))
    rig.wait_until_analyses_run(qtbot)
    rig.session.playback.seek(2)
    QCoreApplication.processEvents()

    assert rig.session.playback.current_frame == 2
    assert rig.panel.histories == []
    assert rig.timing.reports == []

    rig.overlay.hold.set()
    rig.wait_for_analyses(2)

    assert rig.shown_spans() == [("a", ((0, 0),))]  # Without fallback only frame 0 matches a sample.
    (report,) = rig.timing.reports
    assert report is not None
    assert (report.total_video_frames, report.total_overlay_frames, report.exact_matches) == (4, 2, 1)
    assert report.tolerance_us == 0
    assert rig.indicator.reports == rig.timing.reports


def test_newest_policy_change_is_the_only_one_shown(qtbot: QtBot) -> None:
    """Analyses of a superseded policy finishing late never replace what the newest policy shows."""
    rig = _AnalysisRig(qtbot, OverlayPersistenceSettings(enabled=False))
    rig.overlay.hold.clear()
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=10_000))
    rig.wait_until_analyses_run(qtbot)
    superseded_hold = rig.overlay.hold
    rig.overlay.hold = threading.Event()
    rig.overlay.hold.set()

    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=30_000))
    rig.wait_for_analyses(2)

    assert rig.shown_spans() == [("a", ((0, 1),))]  # The 20 ms sample is within 30 ms of frame 1 at 40 ms.
    superseded_hold.set()
    rig.wait_for_analyses(2)

    assert rig.shown_spans() == [("a", ((0, 1),))]
    assert len(rig.panel.histories) == 1
    assert [report.tolerance_us for report in rig.timing.reports if report is not None] == [30_000]


def test_sticky_overlay_changes_place_the_history_again(qtbot: QtBot) -> None:
    """History follows sticky overlays: retained samples keep an object shown past the fallback tolerance."""
    rig = _AnalysisRig(qtbot)

    rig.lane.present_frame(replace(make_frame(3), frame_id=FrameIdentifier(3, 120_000)))
    rig.lane.refresh_scene_history()
    rig.wait_for_analyses(1)
    assert rig.shown_spans() == [("a", ((0, 3),))]
    assert rig.display.frames[-1].overlays is not None

    rig.lane.apply_overlay_persistence(OverlayPersistenceSettings(enabled=False))
    assert rig.display.frames[-1].overlays is None
    rig.wait_for_analyses(1)
    assert rig.shown_spans() == [("a", ((0, 1),))]

    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=110_000))
    assert rig.display.frames[-1].overlays is not None
    assert rig.display.frame_ids() == [3, 3, 3]
    assert cast(PlaybackSource, rig.lane.video_source).jump_calls == []
    rig.wait_for_analyses(2)
    assert rig.shown_spans() == [("a", ((0, 3),))]
    assert rig.timing.reports[-1] is not None and rig.timing.reports[-1].tolerance_us == 110_000


def test_ending_the_session_drops_analysis_still_running(qtbot: QtBot) -> None:
    """An analysis finishing after teardown shows nothing and never reaches back into the released source."""
    rig = _AnalysisRig(qtbot)
    rig.overlay.hold.clear()
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY))
    rig.wait_until_analyses_run(qtbot)
    requested = len(rig.overlay.snapshots)

    rig.session.cleanup()
    rig.overlay.hold.set()
    rig.wait_for_analyses(2)

    assert rig.panel.histories == []
    assert rig.timing.reports == []
    assert rig.indicator.reports == []
    assert len(rig.overlay.snapshots) == requested
    assert all(snapshot.stopped_early for snapshot in rig.overlay.snapshots)
    QCoreApplication.sendPostedEvents(rig.session, QEvent.Type.DeferredDelete)


def test_superseded_analyses_stop_early_instead_of_occupying_the_workers(qtbot: QtBot) -> None:
    """Rapid policy changes leave no obsolete whole-file work running beside the analyses the user is waiting for."""
    rig = _AnalysisRig(qtbot, OverlayPersistenceSettings(enabled=False))
    rig.overlay.hold.clear()
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=10_000))
    rig.wait_until_analyses_run(qtbot)
    superseded = list(rig.overlay.snapshots)
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=30_000))
    rig.wait_until_analyses_run(qtbot)

    rig.overlay.hold.set()
    rig.wait_for_analyses(4)

    assert [snapshot.stopped_early for snapshot in superseded] == [True, True]
    assert not any(snapshot.stopped_early for snapshot in rig.overlay.snapshots[len(superseded) :])
    assert rig.shown_spans() == [("a", ((0, 1),))]


def test_failed_analysis_marks_the_last_history_and_report_out_of_date_until_a_retry_succeeds(
    qtbot: QtBot, caplog: pytest.LogCaptureFixture
) -> None:
    """A policy whose analysis fails keeps the previous results usable but says, per lane, that they are old and why."""
    rig = _AnalysisRig(qtbot, OverlayPersistenceSettings(enabled=False))
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY))
    rig.wait_for_analyses(2)
    last_report = rig.timing.reports[-1]

    rig.overlay.failure = "frame index unreadable"
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=30_000))
    rig.wait_for_analyses(2)

    assert rig.shown_spans() == [("a", ((0, 0),))]
    assert rig.timing.reports == [last_report]
    assert len(rig.panel.histories) == 1
    (history_notice,) = rig.panel.notices
    (alignment_notice,) = rig.timing.notices
    assert rig.indicator.notices == [alignment_notice]
    for notice in (history_notice, alignment_notice):
        assert "lane 'lane'" in notice
        assert "frame index unreadable" in notice
    assert "frame index unreadable" in caplog.text
    assert "scene history of lane 'lane'" in caplog.text
    assert "overlay alignment of lane 'lane'" in caplog.text

    rig.overlay.failure = None
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=30_000))
    rig.wait_for_analyses(2)

    assert rig.shown_spans() == [("a", ((0, 1),))]
    assert len(rig.panel.histories) == 2
    assert [report.tolerance_us for report in rig.timing.reports if report is not None][-1] == 30_000
    assert rig.indicator.reports == rig.timing.reports
    assert len(rig.panel.notices) == len(rig.timing.notices) == 1  # Recovery adds no notice.


def test_stale_notices_name_only_the_settings_that_retry_what_failed(qtbot: QtBot) -> None:
    """A sticky change refreshes only the history, so only its notice offers it; a sync change refreshes both."""
    rig = _AnalysisRig(qtbot, OverlayPersistenceSettings(enabled=False))
    rig.overlay.failure = "report unreadable"
    rig.overlay.failure_spares_history = True

    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=30_000))
    rig.wait_for_analyses(2)

    assert rig.panel.notices == []
    (alignment_notice,) = rig.timing.notices
    assert "sync" in alignment_notice
    assert "sticky" not in alignment_notice

    rig.overlay.failure = "history unreadable"
    rig.overlay.failure_spares_history = False
    rig.lane.refresh_scene_history()
    rig.wait_for_analyses(1)

    (history_notice,) = rig.panel.notices
    assert "sync or sticky" in history_notice

    rig.overlay.failure = None
    rig.lane.apply_timestamp_policy(TimestampFallbackPolicy(tolerance_us=10_000))
    rig.wait_for_analyses(2)

    assert [report.tolerance_us for report in rig.timing.reports if report is not None] == [10_000]
    assert len(rig.panel.histories) == 2


def test_session_pause_and_seek_flush_the_actual_displayed_inspection(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playback controls must release a pending table snapshot, including a seek while still playing."""
    monkeypatch.setattr("ax_devil.modules.video_viewer.media_tools.media_tools_panel.monotonic", lambda: 0.0)
    panel = MediaToolsPanel(render_catalog_selection, filter_model=SessionFilter(build_default_filter_config()))
    qtbot.addWidget(panel)
    panel.show()
    source, display = PlaybackSource(), FakeDisplay()
    overlay = FakeOverlaySource(make_overlay(0))
    lane = make_lane(display, overlay_source=overlay, video_source=source, tools_panel=panel)
    session = make_session(qtbot, [lane], [source])

    def present(frame: int) -> None:
        overlay.overlay = make_overlay(frame)
        overlay.overlay.content.add_entity(Entity(EntityId(str(frame))))
        source.frameReady.emit(make_frame(frame, position_generation=source.position_generation))
        qtbot.waitUntil(lambda: display.frame_ids()[-1:] == [frame])

    def shown() -> list[str]:
        view = next(view for view in panel.objects_page.findChildren(RowTableView) if view.isVisible())
        model = view.rows_model
        return [cast(ObjectPageKey, model.key_at(row)).entity_id for row in range(model.rowCount())]

    session.playback.play()
    present(0)
    present(1)
    assert shown() == ["0"]
    session.playback.pause()
    assert shown() == ["1"]
    session.playback.step(-1)
    present(0)
    assert shown() == ["0"]
    session.playback.play()
    present(1)
    present(2)
    assert shown() != ["2"]
    session.playback.seek(9)
    present(9)
    assert shown() == ["9"]
