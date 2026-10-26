"""Helpers for the offline viewer tests: content builders, widget lookups and the base test class."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QTabBar,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.draggable import DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.row_table import RowTableView
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.video_viewer.offline_viewer_runtime import OfflineSession
from ax_devil.modules.video_viewer.timing_diagnostics_widget import TimingDiagnosticsWidget
from ax_devil.modules.workspace.core import (
    EntryLane,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.offline_sources import (
    TEST_FRAME_SOURCE_OPENERS,
    make_overlay_content,
    stub_frame_source,
)


def count_frame_viewports() -> int:
    """Count live frame viewports across the current Qt application."""

    return sum(isinstance(candidate, FrameViewport) for candidate in QApplication.allWidgets())


def assert_no_stale_renderers(widget: OfflineVideoViewerWidget, baseline_frame_viewports: int) -> None:
    """Assert only the shown lanes' viewports exist."""

    live = len(widget.findChildren(FrameViewport))
    assert count_frame_viewports() == baseline_frame_viewports + live


def lane_displays(widget: OfflineVideoViewerWidget) -> list[FrameDisplay]:
    """Return the lane displays of the shown entry, in lane order."""

    displays: list[FrameDisplay] = widget.findChildren(FrameDisplay)
    return displays


def show_events(widget: OfflineVideoViewerWidget) -> None:
    """Show the viewer with its side panels open on their Events tab; the page lists events only while shown."""

    widget.show()
    for side_panel in widget.findChildren(DraggablePanel):
        side_panel.expand()
    for tabs in widget.findChildren(QTabBar, "mediaToolsTabs"):
        tabs.setCurrentIndex(1)


def events_view(panel: MediaToolsPanel) -> RowTableView:
    view = panel.events_page.findChild(RowTableView)
    assert view is not None
    return view


def event_texts(panel: MediaToolsPanel) -> list[str]:
    """Return the labels of the events the panel lists now."""

    model = events_view(panel).rows_model
    return [cast(Any, model.row_at(row)).event.label for row in range(model.rowCount())]


def alignment_text(timing: TimingDiagnosticsWidget) -> str:
    """Return the status text the timing widget shows now."""

    return "\n".join(label.text() for label in timing.findChildren(QLabel))


def transports(widget: OfflineVideoViewerWidget) -> list[SeekableVideoControlPanel]:
    """Return the playback control bars the viewer shows."""

    panels: list[SeekableVideoControlPanel] = widget.findChildren(SeekableVideoControlPanel)
    return [panel for panel in panels if panel.isVisibleTo(widget)]


def nav_button(widget: OfflineVideoViewerWidget, name: str) -> QPushButton:
    buttons: list[QPushButton] = widget.findChildren(QPushButton)
    return next(button for button in buttons if button.accessibleName() == name)


def nav_label(widget: OfflineVideoViewerWidget) -> QLabel:
    labels: list[QLabel] = widget.findChildren(QLabel)
    return next(label for label in labels if label.text().startswith("Entry "))


def shown_entry_index(widget: OfflineVideoViewerWidget) -> int:
    """Return the index of the entry the viewer reports as on screen."""
    item = widget.current_on_screen_item()
    assert item is not None
    return widget._content.bookmark_entries.index(item.entry)


def shown_session(widget: OfflineVideoViewerWidget) -> OfflineSession:
    """Return the shown entry's session, for lane state the viewer does not expose (presenters, overlay sources)."""

    runtime = widget._runtime  # noqa: SLF001
    assert runtime is not None
    return runtime


def playlist_content(*names: str) -> PlaylistContent:
    return PlaylistContent(
        display_name="Test Playlist",
        entries=tuple(
            PlaylistEntry(lanes=make_local_content(name).standalone_lanes(), default_considered=True) for name in names
        ),
    )


def lane_entry(*videos: SeekableVideoContent) -> PlaylistEntry:
    """Build one entry comparing *videos* side by side."""
    return PlaylistEntry(
        lanes=tuple(
            EntryLane(display_name=video.display_name, video=video, default_considered=True) for video in videos
        ),
        default_considered=True,
    )


def warning_text(widget: OfflineVideoViewerWidget) -> str:
    """Return the text of the one banner the viewer shows."""
    (banner,) = [banner for banner in widget.findChildren(Banner) if not banner.isHidden()]
    return " ".join(label.text() for label in banner.findChildren(QLabel))


class DeferredBackground(BackgroundJobs):
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


def make_seekable_content(
    name: str,
    *,
    frame_source_opener: Callable[[], object] = stub_frame_source,
    overlays: tuple[OverlayContent, ...] = (),
) -> SeekableVideoContent:
    source_spec = FileVideoSourceSpec(path=Path(f"/tmp/{name}.mp4"))
    TEST_FRAME_SOURCE_OPENERS[source_spec.path] = frame_source_opener
    return SeekableVideoContent(
        display_name=name,
        source_spec=source_spec,
        overlays=overlays,
    )


def make_local_content(name: str, *, with_overlay: bool = False) -> SeekableVideoContent:
    """Create seekable video content with spec-backed source stubs for testing."""
    overlay_specs: tuple[OverlayContent, ...] = ()
    if with_overlay:
        overlay_specs = (make_overlay_content("overlay"),)
    return make_seekable_content(name, overlays=overlay_specs)


def attach_offline_widget(qtbot: QtBot, widget: OfflineVideoViewerWidget) -> None:
    """Attach an offline viewer in tests and start its workspace lifecycle."""
    qtbot.addWidget(widget)
    widget.on_workspace_attached()


class OfflineViewerTest:
    """Base of the offline viewer test classes: opens viewers over the shared render catalog manager."""

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
        attach_offline_widget(qtbot, widget)
        return widget


def playlist_entry(*paths: Path) -> PlaylistEntry:
    videos = [
        SeekableVideoContent(display_name=path.name, source_spec=FileVideoSourceSpec(path=path)) for path in paths
    ]
    return PlaylistEntry(
        lanes=tuple(
            EntryLane(display_name=video.display_name, video=video, default_considered=True) for video in videos
        ),
        default_considered=True,
    )
