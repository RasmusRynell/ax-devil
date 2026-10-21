"""Building the displays of an offline entry's lanes over already opened media."""

from __future__ import annotations

from contextlib import ExitStack
from functools import partial

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering import (
    OverlayVisibility,
    SceneRenderCatalogManager,
    SceneRenderCatalogSelection,
)
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition
from ax_devil.modules.video_viewer.lane_grid import lane_grid_columns, lane_grid_rows
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_entry_media import (
    OpenedLane,
)
from ax_devil.modules.video_viewer.offline_frame_pool import PooledVideoSource
from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.overlay_persistence import (
    OverlayPersistenceSettings,
)
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter
from ax_devil.modules.video_viewer.timing_diagnostics_widget import (
    OverlayAlignmentIndicator,
    TimingDiagnosticsWidget,
)
from ax_devil.modules.workspace.core import EntryLane


def create_lane_layout(container: QWidget, lane_count: int) -> QGridLayout:
    grid_layout = QGridLayout(container)
    grid_layout.setContentsMargins(0, 0, 0, 0)
    grid_layout.setHorizontalSpacing(Space.XS)
    grid_layout.setVerticalSpacing(Space.XS)

    if lane_count == 0:
        return grid_layout

    columns = lane_grid_columns(lane_count)
    rows = lane_grid_rows(lane_count)
    for column in range(columns):
        grid_layout.setColumnStretch(column, 1)
    for row in range(rows):
        grid_layout.setRowStretch(row, 1)
    return grid_layout


def add_lane_display(
    parent_widget: QWidget,
    lane_layout: QGridLayout,
    lane_count: int,
    lane_index: int,
    lane: EntryLane,
    display: FrameDisplay,
    pane_title: str,
) -> OverlayAlignmentIndicator:
    pane = QWidget(parent_widget)
    pane_layout = QVBoxLayout(pane)
    pane_layout.setContentsMargins(0, 0, 0, 0)
    pane_layout.setSpacing(0)

    lane_name = lane.display_name or lane.video.display_name
    if lane_count > 1 or lane_name != pane_title:
        name_label = QLabel(lane_name, display.viewport)
        name_label.setObjectName("lane-indicator-label")
        name_label.setStyleSheet(
            f"background-color: rgba(0, 0, 0, 160); border-radius: {Radius.CONTROL}px; color: white;"
            f"padding: {Space.XS}px {Space.M}px;"
        )
        display.mount_overlay(
            name_label,
            position=OverlayPosition.FRAME_TOP_LEFT,
            hide_while_info_shown=True,
            preference=OverlayPreference.LANE_NAMES,
        )
    alignment_indicator = OverlayAlignmentIndicator(display.viewport)
    display.mount_overlay(alignment_indicator, position=OverlayPosition.FRAME_TOP_RIGHT)
    pane_layout.addWidget(display, 1)

    columns = lane_grid_columns(lane_count)
    row, column = divmod(lane_index, columns)
    lane_layout.addWidget(pane, row, column)
    return alignment_indicator


def build_lane(
    parent_widget: QWidget,
    opened: OpenedLane,
    position: int,
    lane_count: int,
    container: QWidget,
    lane_layout: QGridLayout,
    *,
    source_pool: list[PooledVideoSource],
    render_catalog_manager: SceneRenderCatalogManager,
    use_lane_controls: bool,
    lane_visibility: dict[int, OverlayVisibility],
    pane_title: str,
    rollback: ExitStack,
) -> OfflineLane:
    lane_content = opened.lane
    video = opened.video
    display = FrameDisplay(initial_focus=False)
    rollback.callback(display.cleanup)
    controls = SeekableVideoControlPanel(display.viewport) if use_lane_controls else None
    if controls is not None:
        display.mount_overlay(controls, position=OverlayPosition.BOTTOM_FULL_WIDTH)
    display.enable_side_panel()
    alignment_indicator = add_lane_display(
        parent_widget, lane_layout, lane_count, position, lane_content, display, pane_title
    )

    pooled_source = source_pool[opened.source_index]
    overlay_source = opened.overlay_source
    overlay_policy = opened.overlay_policy
    timing_controls = TimingDiagnosticsWidget()
    render_catalog_selection = render_catalog_manager.create_selection(
        visibility=lane_visibility.get(opened.lane_index, OverlayVisibility()), parent=container
    )

    def remember_visibility(
        *, selection: SceneRenderCatalogSelection = render_catalog_selection, lane_index: int = opened.lane_index
    ) -> None:
        lane_visibility[lane_index] = selection.visibility

    render_catalog_selection.selectionChanged.connect(remember_visibility)
    filter_model = SessionFilter(overlay_source.get_filter_config() if overlay_source is not None else None)
    export_action = QAction(Icon.EXPORT.icon(), "Export video", display)
    tools_panel = MediaToolsPanel(
        render_catalog_selection,
        filter_model=filter_model,
        overlay_settings=OverlayPersistenceSettings.default_enabled(),
        export_action=export_action,
        scene_history=opened.scene_history,
        timing_controls=timing_controls,
    )
    presenter = SceneFramePresenter(
        scene_filter=filter_model,
        scene_render_catalog=render_catalog_selection.active_catalog(),
        await_catalog=True,
    )

    def refresh_display_overlays(_catalog: object, *, lane_display: FrameDisplay = display) -> None:
        lane_display.refresh_overlays()

    display.set_side_panel_widget(tools_panel)
    tools_panel.link_selection(display)
    filter_model.changed.connect(display.refresh_overlays)
    render_catalog_selection.activeCatalogChanged.connect(presenter.attach_scene_render_catalog)
    render_catalog_selection.activeCatalogChanged.connect(refresh_display_overlays)
    tools_panel.catalogViewerRequested.connect(partial(open_catalog_viewer, container, render_catalog_selection))
    if overlay_policy is not None:
        tools_panel.overlayPersistenceChanged.connect(overlay_policy.update_settings)

    lane = OfflineLane(
        content=video,
        name=lane_content.display_name,
        display=display,
        overlay_source=overlay_source,
        export_action=export_action,
        lane_index=opened.lane_index,
        controls=controls,
        overlay_policy=overlay_policy,
        presenter=presenter,
        tools_panel=tools_panel,
        filter_model=filter_model,
        timing_controls=timing_controls,
        alignment_indicator=alignment_indicator,
        video_source=pooled_source.source,
        source_index=pooled_source.source_index,
    )
    # Connected after the presenter's own, so the lane sees the catalog the presenter has just taken.
    render_catalog_selection.activeCatalogChanged.connect(lambda _catalog, lane=lane: lane.update_export_action())
    lane.update_export_action()
    lane.initialize_timing_diagnostics(opened.alignment_report)
    return lane


def open_catalog_viewer(parent: QWidget, render_catalog_selection: SceneRenderCatalogSelection) -> None:
    from ax_devil.modules.catalog_viewer import show_catalog_viewer

    show_catalog_viewer(
        render_catalog_selection.manager,
        parent=parent,
        catalog_path=render_catalog_selection.active_catalog_path(),
    )
