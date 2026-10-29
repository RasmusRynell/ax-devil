"""Tests for lane history, overlay analysis and export of the offline video viewer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QToolButton,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import SceneRenderCatalogManager, SceneRenderCatalogStore
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy
from ax_devil.modules.video_viewer import (
    scene_frame_presenter,
)
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.timing_diagnostics_widget import OverlayAlignmentIndicator, TimingDiagnosticsWidget
from ax_devil.modules.workspace.core import (
    PlaylistContent,
    PlaylistEntry,
)
from tests.catalog_helpers import GatedCatalogLoader
from tests.helpers.offline_sources import (
    SpecFileOverlaySource,
    TrackedFrameSource,
    TrackedOverlaySource,
    frame_data,
    make_overlay_content,
    make_tracked_frame_source_opener,
)
from tests.helpers.offline_viewer import (
    OfflineViewerTest,
    alignment_text,
    attach_offline_widget,
    event_texts,
    events_view,
    make_local_content,
    make_seekable_content,
    show_events,
    shown_session,
)


class TestOfflineViewerAnalysisExport(OfflineViewerTest):
    """Lane history follows playback and settings; export freezes what the lane shows."""

    def test_lane_history_follows_playback_and_jumps_to_requested_frames(self, qtbot: QtBot) -> None:
        """Each lane lists its overlay's history, follows the displayed frame and seeks on request."""
        frame_sources: list[TrackedFrameSource] = []
        content = make_seekable_content(
            "events",
            frame_source_opener=make_tracked_frame_source_opener("video", frame_sources),
            overlays=(make_overlay_content("first"), make_overlay_content("second")),
        )
        widget = self._open(qtbot, content)
        panels = widget.findChildren(MediaToolsPanel)
        show_events(widget)
        event_pages = [panel.events_page for panel in panels]
        assert all(page.isVisible() for page in event_pages)
        assert [event_texts(panel) for panel in panels] == [["Delete first"], ["Delete second"]]

        panels[1].frameRequested.emit(20)
        assert frame_sources[0].get_current_frame() == 20
        frame_sources[0].frameReady.emit(frame_data(20))
        QCoreApplication.processEvents()
        qtbot.waitUntil(lambda: [cast(Any, events_view(panel)).position for panel in panels] == [(0, 1), (0, 1)])

        panels[0].frameRequested.emit(12)
        assert frame_sources[0].get_current_frame() == 12

    def test_lane_history_follows_sticky_and_fallback_settings(self, qtbot: QtBot) -> None:
        """Each sticky or fallback change shows a history and alignment report made under the new setting."""
        overlay_sources: list[TrackedOverlaySource] = []
        content = make_seekable_content("history", overlays=(make_overlay_content("only", overlay_sources),))
        widget = self._open(qtbot, content)
        (panel,) = widget.findChildren(MediaToolsPanel)
        (timing,) = widget.findChildren(TimingDiagnosticsWidget)
        (source,) = overlay_sources
        assert isinstance(source, SpecFileOverlaySource)
        show_events(widget)
        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=True, timeout_ms=1500))
        qtbot.waitUntil(lambda: source.history_selections[-1] == (True, 1_500_000), timeout=5000)

        def shows(history: str, tolerance: str) -> Callable[[], bool]:
            def shown() -> bool:
                alignment = alignment_text(timing)
                return event_texts(panel) == [history] and f"tol {tolerance}" in alignment

            return shown

        qtbot.waitUntil(shows("Delete only", "50 ms"), timeout=5000)

        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=False))
        qtbot.waitUntil(lambda: event_texts(panel) == ["Delete only (no sticky)"], timeout=5000)
        assert source.history_selections[-1] == (False, None)

        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=0))
        qtbot.waitUntil(shows("Delete only (no fallback, no sticky)", "0 ms"), timeout=5000)

        panel.overlay_controls.settingsChanged.emit(OverlayPersistenceSettings(enabled=True))
        qtbot.waitUntil(shows("Delete only (no fallback)", "0 ms"), timeout=5000)

    def test_failed_lane_analysis_is_shown_and_the_next_setting_change_recovers(self, qtbot: QtBot) -> None:
        """A history or alignment analysis that fails says so on its lane, keeps the old results and can be retried."""
        overlay_sources: list[TrackedOverlaySource] = []
        content = make_seekable_content("history", overlays=(make_overlay_content("only", overlay_sources),))
        widget = self._open(qtbot, content)
        (panel,) = widget.findChildren(MediaToolsPanel)
        (timing,) = widget.findChildren(TimingDiagnosticsWidget)
        (indicator,) = widget.findChildren(OverlayAlignmentIndicator)
        (source,) = overlay_sources
        assert isinstance(source, SpecFileOverlaySource)
        notice = panel.findChild(QLabel, "historyOutOfDateNotice")
        assert notice is not None
        show_events(widget)
        qtbot.waitUntil(lambda: "tol 50 ms" in alignment_text(timing), timeout=5000)
        assert notice.isHidden()

        source.failure = "frame index unreadable"
        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=0))

        qtbot.waitUntil(lambda: not notice.isHidden() and not indicator.isHidden(), timeout=5000)
        for text in (notice.text(), alignment_text(timing), indicator.toolTip()):
            assert "lane '" in text
            assert "frame index unreadable" in text
        assert event_texts(panel) == ["Delete only"]
        assert "tol 50 ms" in alignment_text(timing)

        source.failure = None
        timing.timestampFallbackPolicyChanged.emit(TimestampFallbackPolicy(tolerance_us=10_000))

        qtbot.waitUntil(lambda: notice.isHidden() and "tol 10 ms" in alignment_text(timing), timeout=5000)
        assert event_texts(panel) == ["Delete only"]
        assert "frame index unreadable" not in alignment_text(timing)
        assert indicator.isHidden()

    def test_overlay_visibility_survives_navigation_and_is_snapshotted_for_export(self, qtbot: QtBot) -> None:
        """Rebuilt playlist lanes keep preferences; an export retains its selected compiled variant."""
        first = make_local_content("first", with_overlay=True)
        second = make_local_content("second", with_overlay=True)
        playlist = PlaylistContent(
            display_name="Playlist",
            entries=(
                PlaylistEntry(lanes=first.standalone_lanes(), default_considered=True),
                PlaylistEntry(lanes=second.standalone_lanes(), default_considered=True),
            ),
        )
        widget = self._open(qtbot, playlist)

        def details(lane: OfflineLane) -> tuple[QPushButton, QPushButton]:
            assert lane.tools_panel is not None
            confidence = lane.tools_panel.findChild(QPushButton, "overlayFeature_confidence")
            reset = lane.tools_panel.findChild(QPushButton, "overlayVisibilityReset")
            assert confidence is not None and reset is not None
            return confidence, reset

        lane = shown_session(widget).lanes[0]
        assert lane.presenter is not None
        full = lane.presenter.scene_render_catalog
        confidence, _reset = details(lane)
        widget.pause_playback()
        with patch.object(lane.display, "refresh_overlays", wraps=lane.display.refresh_overlays) as refresh:
            confidence.setChecked(False)
            refresh.assert_called_once()
        assert not shown_session(widget).playback.is_playing
        assert lane.presenter.scene_render_catalog is not full
        widget.step_next_entry()
        lane = shown_session(widget).lanes[0]
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
        confidence, _reset = details(shown_session(widget).lanes[0])
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
                make_local_content("cold", with_overlay=True), render_catalog_manager=manager
            )
            attach_offline_widget(qtbot, widget)
            lane = shown_session(widget).lanes[0]
            assert lane.tools_panel is not None and lane.presenter is not None
            more_button = lane.tools_panel.findChild(QToolButton, "mediaToolsMoreButton")
            assert more_button is not None and more_button.menu() is not None
            assert lane.export_action in more_button.menu().actions()
            assert lane.presenter.scene_render_catalog is None and not lane.export_action.isEnabled()

            with (
                patch("ax_devil.modules.video_viewer.export.ExportDialog") as dialog_type,
                patch("ax_devil.modules.video_viewer.offline_export.QMessageBox.warning") as told,
            ):
                widget.export_video()
                told.assert_called()
                assert "still loading" in told.call_args.args[2]
                told.reset_mock()
                shown_session(widget).export_lanes([lane])
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
