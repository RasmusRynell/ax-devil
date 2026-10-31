"""Media tools panel screenshot scenarios, rendered only inside the isolated pytest display."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.app import Application
from ax_devil.modules.filtering.measure_range import Measure, MeasureRange
from ax_devil.modules.object_inspection.pinned_fields import FieldId
from ax_devil.modules.plugin_system import RuntimePluginRegistry
from ax_devil.modules.scene.model import EntityId
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.appearance_popover import AppearancePopover
from ax_devil.modules.video_viewer.media_tools.object_scope import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey
from ax_devil.modules.workspace.core import OverlayFile, PlaylistItem, VideoItem, Workspace, save_workspace
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML
from tools.ui_samples import CLIP_FPS, CLIP_FRAMES, write_tracks
from tools.ui_shots_config import GRID_SIZE, OUT, WINDOW_SIZES, configured
from tools.ui_shots_media_tools import (
    FourCameras,
    click_object,
    grid_panels,
    move_pointer,
    object_point,
    save_shot,
    with_merge_and_split,
)
from tools.ui_shots_navigation import open_video, trigger


def media_tools_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the media tools panel on one video, its filter drawer and popups, and a 2x2 grid with a panel per lane.

    Add a shot with one more ``shoot`` call.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in (
        *OUT.glob(f"{theme}-panel-*.png"),
        *OUT.glob(f"{theme}-grid*.png"),
        *OUT.glob(f"{theme}-hover-chip.png"),
        *OUT.glob(f"{theme}-selected-object.png"),
    ):
        stale.unlink()
    config = configured(theme)
    clip = video_file_factory(CLIP_FRAMES / CLIP_FPS, CLIP_FPS)
    tracks = write_tracks(tmp_path / "tracks.xml")
    cameras = []
    for index in range(4):
        cameras.append(tmp_path / f"cam-{index + 1}.mp4")
        shutil.copyfile(clip, cameras[-1])
    workspace_path = tmp_path / "Media tools.ax-devil.workspace"
    save_workspace(
        Workspace(
            name="Media tools",
            items=(
                VideoItem(video=clip, overlays=(OverlayFile(tracks, ONVIF_XML),), label="Tracked video"),
                PlaylistItem(
                    resolver=FourCameras.plugin_id(),
                    settings={"videos": [str(camera) for camera in cameras], "overlay": str(tracks)},
                    label="Four cameras",
                ),
            ),
        ),
        workspace_path,
    )
    # The registry is restored when the test ends. The application loads the built-in plugins once per process.
    monkeypatch.setattr(
        RuntimePluginRegistry,
        "_plugins_by_type",
        {k: dict(v) for k, v in RuntimePluginRegistry._plugins_by_type.items()},
    )
    RuntimePluginRegistry.register_plugin(FourCameras, None)
    window = Application(config_path=config.config_path, workspace_file=workspace_path).start(qapp)
    qtbot.addWidget(window)
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.

    def shoot(name: str, popup: QWidget | None = None, anchor: QWidget | None = None) -> None:
        qtbot.wait(300)  # Let the query debounce and the lists catch up.
        save_shot(window, theme, name, popup, anchor)

    def shoot_appearance(name: str, lane_panel: MediaToolsPanel) -> None:
        button = lane_panel.findChild(QToolButton, "mediaToolsAppearanceButton")
        popover = lane_panel.findChild(AppearancePopover)
        assert button is not None and popover is not None
        button.click()
        qtbot.wait(200)
        shoot(name, popover, lane_panel._tab_row)
        popover.close()

    # One video: the SHOWN_FRAME has objects 1, 2, 4, 5, 7; object 6 ended earlier and 8 ends one frame before.
    window.resize(*WINDOW_SIZES["wide"])
    viewer = open_video(qtbot, window, "Tracked video")
    trigger(window, "Toggle Media Tools Panel")
    qtbot.wait(500)
    panel = viewer.findChild(MediaToolsPanel)
    assert panel is not None
    objects = panel.objects_page
    shoot("panel-objects")
    objects.set_scope(ObjectScope.FILE)
    shoot("panel-objects-file")
    objects.set_scope(ObjectScope.FRAME)
    objects._query_bar.set_text("class:human conf>0.5")
    shoot("panel-objects-query")
    objects._query_bar.set_text("")
    # The filter drawer with one class hidden and a lowest score.
    filters = panel.filter_model
    filter_button = panel.findChild(QToolButton, "objectsFilterButton")
    assert filter_button is not None
    filter_button.click()
    filters.set_enabled(filters.options[0].id, False)
    filters.set_range(Measure.SCORE, MeasureRange(0.5, None))
    shoot("panel-objects-filter")
    filters.reset()
    filter_button.click()
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    move_pointer(viewport, object_point(viewport, "1"))
    shoot("hover-chip")
    click_object(viewport, "1")
    shoot("selected-object")
    panel._stack.show_root(objects)
    panel.show_object("7")
    shoot("panel-object")
    # Pin three fields on object 7, which has them, then follow object 1, which lacks one of them.
    scene = panel._last_scene
    assert scene is not None and panel._last_frame_id is not None
    debug_values: dict[str, dict[str, Any]] = {
        "7": {"movement": {"moving": True, "stationary_s": 0.0}},
        "1": {"movement": {"moving": False}},
    }
    for entity_id, debug in debug_values.items():
        observation = scene.entities[EntityId(entity_id)].latest_observation
        assert observation is not None
        observation.debug = debug
    for field_id in (
        FieldId("Debug", ("movement", "moving")),
        FieldId("Debug", ("movement", "stationary_s")),
        FieldId("Classification", ("conf",)),
    ):
        panel._pinned_fields.toggle(field_id)
    assert panel._followed is not None
    panel._followed.update_scene(scene, panel._last_frame_id.sequence_id)
    shoot("panel-object-pinned")
    panel.show_object("1")
    shoot("panel-object-pinned-unavailable")
    panel.show_object("7")
    panel._pinned_fields.toggle(FieldId("Debug", ("movement", "stationary_s")))
    panel._pinned_fields.toggle(FieldId("Debug", ("movement", "moving")))
    panel._pinned_fields.toggle(FieldId("Classification", ("conf",)))
    panel._followed.update_scene(scene, panel._last_frame_id.sequence_id)
    panel.show_object("6")
    shoot("panel-object-absent")
    panel._stack.show_root(objects)
    qtbot.wait(200)
    more_button = panel._tab_row.more_button
    panel._tab_row.more_menu.popup(more_button.mapToGlobal(QPoint(0, more_button.height())))
    qtbot.wait(200)
    shoot("panel-menu", panel._tab_row.more_menu, more_button)
    panel._tab_row.more_menu.close()
    shoot_appearance("panel-appearance", panel)

    # The Events tab: past rows above the now line at SHOWN_FRAME, upcoming ones below it.
    rename = with_merge_and_split(panel)
    panel._tab_row.tabs.setCurrentIndex(1)
    shoot("panel-events")
    events = panel.events_page
    events._query_bar.set_text("kind:merge")
    shoot("panel-events-query")
    events._query_bar.set_text("")
    events.eventActivated.emit(EventPageKey(rename))
    shoot("panel-event")

    # Four lanes of the same clip, the panel open on Objects in each, then an object page in each.
    window.resize(*GRID_SIZE)
    grid = open_video(qtbot, window, "Four cameras")
    qtbot.waitUntil(lambda: len(grid.findChildren(FrameViewport)) == len(cameras), timeout=10000)
    qtbot.wait(1500)
    panels = grid_panels(qtbot, grid)
    shoot("grid")
    for lane_panel in panels:
        with_merge_and_split(lane_panel)
        lane_panel._tab_row.tabs.setCurrentIndex(1)
    shoot("grid-events")
    for lane_panel in panels:
        lane_panel._tab_row.tabs.setCurrentIndex(0)
    selected_lane = grid.findChildren(FrameViewport)[1]
    click_object(selected_lane, "2")
    shoot("grid-selected")
    for lane_panel in panels:
        lane_panel._stack.show_root(lane_panel.objects_page)
    for lane_panel in panels:
        lane_panel.show_object("7")
    qtbot.wait(500)
    shoot("grid-object")
    # One lane on another catalog, so the first lane's popover names it and offers Apply to all.
    minimal = next(path for path in BUILT_IN_CATALOG_PATHS if path.stem == "minimal")
    panels[1]._render_catalog_selection.select_catalog(minimal)
    panels[0]._stack.show_root(panels[0].objects_page)
    shoot_appearance("grid-appearance", panels[0])
