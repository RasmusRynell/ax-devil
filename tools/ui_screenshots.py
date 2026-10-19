"""Save offscreen screenshots of the main UI surfaces, for checking a UI change before and after.

The video surfaces show a generated clip with a small ONVIF metadata file, so the canvas has boxes and labels and the
media tools list entities and events. Runs inside the test suite's isolated home folder, so it never reads or changes
the user's settings, and never opens a window on the desktop. Text sizes switch live in one window per theme, as the
Settings dialog does::

    PYTHONPATH=. uv run pytest -p tests.conftest tools/ui_screenshots.py -q
    PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/after uv run pytest -p tests.conftest tools/ui_screenshots.py -q

``test_media_tools_screenshots`` adds ``<theme>-panel-*.png``, ``<theme>-grid*.png``, ``<theme>-hover-chip.png`` and
``<theme>-selected-object.png`` shots of the media tools panel and of picking objects on the video; run it alone with
``-k media_tools``.

Shots go to ``AX_DEVIL_UI_SHOTS`` (default ``/tmp/ax-devil-ui-shots``) as ``<theme>-<text size>-<surface>.png``.
They are rendered at ``AX_DEVIL_UI_SHOTS_SCALE`` device pixels per logical pixel (default 2, as on a high-DPI screen).
Dialogs larger than a window size get an extra ``-wide`` or ``-narrow`` shot clamped to it. Each run first deletes
the theme's earlier shots there, so a folder reused across runs holds only current shots.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QMenu,
    QTabBar,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.app import Application
from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.plugin_system import (
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.video_player.engine.data_types import HoverHit
from ax_devil.modules.video_player.ui.hover_chip import HoverChip
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.live_connection import LiveConnectionStatus, LiveFeed, LiveFeedStatus
from ax_devil.modules.video_viewer.live_video_viewer import LiveStatusPanel
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.object_scope import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey
from ax_devil.modules.workspace.core import (
    OverlayFile,
    PlaylistItem,
    VideoItem,
    Workspace,
    save_workspace,
)
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML
from tools.ui_samples import CLIP_FPS, CLIP_FRAMES, sample_workspace, write_tracks
from tools.ui_shots_config import GRID_SIZE, OUT, TEXT_SIZES, WINDOW_SIZES, configured
from tools.ui_shots_media_tools import (
    FourCameras,
    click_object,
    grid_panels,
    move_pointer,
    object_point,
    save_shot,
    with_merge_and_split,
)
from tools.ui_shots_navigation import (
    fill_sidebar_views,
    grab_next_dialog,
    open_video,
    select_combo,
    select_tab,
    show_sidebar,
    tree_row,
    trigger,
)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the welcome screen, tracked video lanes with media tools, the catalog viewer and the main dialogs.

    Switches text size live.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob(f"{theme}-*.png"):
        stale.unlink()
    config = configured(theme)
    settings = GlobalSettings()
    clip_path = video_file_factory(CLIP_FRAMES / CLIP_FPS, CLIP_FPS)
    workspace_path = sample_workspace(tmp_path, clip_path)
    recent_path = Path(config.get_saved("storage")["base_dir"]) / "recent-workspaces.json"
    recent_path.write_text(
        json.dumps(
            [
                {
                    "path": str(workspace_path if days == 0 else tmp_path / f"{name}.ax-devil.workspace"),
                    "used": (datetime.now() - timedelta(days=days)).isoformat(),
                }
                for days, name in ((0, "Parking lot"), (2, "Entrance cameras"), (40, "Exp 3"))
            ]
        )
    )
    # Abbreviate scenario paths as ~/... so pytest's per-run directory does not change the welcome shots.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    application = Application(config_path=config.config_path)
    window = application.start(qapp)
    qtbot.addWidget(window)
    qtbot.wait(100)  # Finish restoring window geometry before choosing screenshot sizes.
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        window.resize(*WINDOW_SIZES["wide"])
        qtbot.wait(200)
        window.grab().save(str(OUT / f"{theme}-{text_size.value}-welcome.png"))

    trigger(window, "Render Catalogs")
    [catalog_viewer] = window.findChildren(CatalogViewerWindow)
    qtbot.waitUntil(lambda: bool(catalog_viewer.sheet_titles()), timeout=10000)
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.
    sheet = catalog_viewer._current_sheet()
    assert sheet is not None
    selected = str(next(iter(sheet.scene.entities)))
    catalog_viewer._renderer.select_target(selected)
    catalog_viewer._renderer.selectionChanged.emit(selected)
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        catalog_viewer.resize(*WINDOW_SIZES["narrow"])
        qtbot.wait(500)
        catalog_viewer.grab().save(str(OUT / f"{theme}-{text_size.value}-catalog-viewer.png"))
    catalog_viewer.hide()
    window.activateWindow()

    window.launch(workspace_file=workspace_path)
    viewer = open_video(qtbot, window, "Tracked video")
    trigger(window, "Media Tools Panel")
    media_tools_tabs = viewer.findChild(QTabBar, "mediaToolsTabs")
    assert media_tools_tabs is not None
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        prefix = f"{theme}-{text_size.value}"
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{prefix}-video-{size_name}.png"))
        media_tools_tabs.setCurrentIndex(1)
        qtbot.wait(200)
        window.grab().save(str(OUT / f"{prefix}-video-events-narrow.png"))
        media_tools_tabs.setCurrentIndex(0)
        for name, action_id in (
            ("settings", "Settings"),
            ("shortcuts", "Keyboard Shortcuts"),
            ("add-video", "Add Video"),
            ("add-live-stream", "Add Live Stream"),
            ("add-playlist", "Add Playlist"),
        ):
            grab_next_dialog(f"{prefix}-{name}")
            trigger(window, action_id)
        grab_next_dialog(f"{prefix}-settings-storage", lambda dialog: select_tab(dialog, "Storage"))
        trigger(window, "Settings")
        grab_next_dialog(f"{prefix}-settings-streams", lambda dialog: select_tab(dialog, "Stream defaults"))
        trigger(window, "Settings")
        grab_next_dialog(f"{prefix}-settings-network-scan", lambda dialog: select_tab(dialog, "Network scan"))
        trigger(window, "Settings")
        grab_next_dialog(
            f"{prefix}-settings-manual-cache",
            lambda dialog: select_combo(dialog, "Manual"),
        )
        trigger(window, "Settings")
        grab_next_dialog(f"{prefix}-quick-setup")
        trigger(window, "Quick Setup")
    # Quick Setup opens with room for the largest text size; picking it must not need scrolling.
    settings.text_size = TextSize.SMALL
    grab_next_dialog(
        f"{theme}-quick-setup-small-to-larger",
        lambda dialog: cast(QuickSetupDialog, dialog).text_size_row.select(TextSize.LARGER),
    )
    trigger(window, "Quick Setup")

    [pin] = [button for button in viewer.findChildren(QAbstractButton) if button.toolTip().startswith("Pin:")]
    QTest.mouseClick(pin, Qt.MouseButton.LeftButton)
    open_video(qtbot, window, "Second lane")
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{theme}-{text_size.value}-two-lanes-{size_name}.png"))

    row = tree_row(window, "Night shift")
    assert row is not None
    row[1].setExpanded(True)
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            suffix = "" if size_name == "wide" else f"-{size_name}"
            window.grab().save(str(OUT / f"{theme}-{text_size.value}-mixed-workspace{suffix}.png"))

    # The other sidebar views over the same workspace, each with some content of its own.
    fill_sidebar_views(qtbot, monkeypatch, window)
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            suffix = "" if size_name == "wide" else f"-{size_name}"
            for view_id in ("live", "offline", "bookmarks", "plugins"):
                show_sidebar(window, view_id.capitalize())
                qtbot.wait(500)
                window.grab().save(str(OUT / f"{theme}-{text_size.value}-view-{view_id}{suffix}.png"))


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_live_status_screenshots(qtbot: QtBot, theme: str) -> None:
    """Save Pause and Retry over the video scrim, including the light theme's contrasting controls."""
    OUT.mkdir(parents=True, exist_ok=True)
    apply_theme(theme)
    parent = QWidget()
    parent.setObjectName("videoStandIn")
    parent.setStyleSheet("#videoStandIn { background: #4a5a46; }")
    qtbot.addWidget(parent)
    layout = QVBoxLayout(parent)
    panel = LiveStatusPanel(parent)
    layout.addWidget(panel)
    panel.show_connection_status(LiveConnectionStatus((LiveFeedStatus(LiveFeed.VIDEO).failed("Connection lost"),)))
    parent.show()
    for text_size in TEXT_SIZES:
        apply_text_size(text_size.body_px)
        for size_name, width in (("wide", 1000), ("narrow", 640)):
            parent.resize(width, parent.sizeHint().height())
            QApplication.processEvents()
            parent.grab().save(str(OUT / f"{theme}-{text_size.value}-live-status-{size_name}.png"))


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_hover_screenshots(qtbot: QtBot, theme: str) -> None:
    """Save the hover chip over a stand-in video, with and without detail, in wide and narrow viewers."""
    OUT.mkdir(parents=True, exist_ok=True)
    apply_theme(theme)
    parent = QWidget()
    parent.setObjectName("videoStandIn")
    parent.setStyleSheet("#videoStandIn { background: #4a5a46; }")
    qtbot.addWidget(parent)
    parent.show()
    chip = HoverChip(parent)
    box = (0.312, 0.142, 0.2, 0.5)
    hits = {
        "detail": HoverHit(
            "12d872dd", box, "12d872dd-1285-536d-bc40-123456789abc person", "conf 0.97 · speed 1.2", "person"
        ),
        "plain": HoverHit("12", box, "12 vehicle_other", "", "vehicle_other"),
    }
    for text_size in TEXT_SIZES:
        apply_text_size(text_size.body_px)
        for size_name, size in (("wide", (1000, 640)), ("narrow", (320, 600))):
            parent.resize(*size)
            for name, hit in hits.items():
                x, y = round(box[0] * size[0] + box[2] * size[0]), round(box[1] * size[1])
                chip.show_for(hit, x, y)
                QApplication.processEvents()
                parent.grab().save(str(OUT / f"{theme}-{text_size.value}-hover-{name}-{size_name}.png"))
                chip.hide_chip()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_media_tools_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the media tools panel on one video, its menu, and a 2x2 lane grid with a panel in every lane.

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

    def shoot(name: str, menu: QMenu | None = None, anchor: QWidget | None = None) -> None:
        qtbot.wait(300)  # Let the query debounce and the lists catch up.
        save_shot(window, theme, name, menu, anchor)

    # One video: the SHOWN_FRAME has objects 1, 2, 4, 5, 7; object 6 ended earlier and 8 ends one frame before.
    window.resize(*WINDOW_SIZES["wide"])
    viewer = open_video(qtbot, window, "Tracked video")
    trigger(window, "Media Tools Panel")
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
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    move_pointer(viewport, object_point(viewport, "1"))
    shoot("hover-chip")
    click_object(viewport, "1")
    shoot("selected-object")
    panel._stack.show_root(objects)
    panel.show_object("7")
    shoot("panel-object")
    panel.show_object("6")
    shoot("panel-object-absent")
    panel._stack.show_root(objects)
    qtbot.wait(200)
    more_button = panel._tab_row.more_button
    panel._tab_row.more_menu.popup(more_button.mapToGlobal(QPoint(0, more_button.height())))
    qtbot.wait(200)
    shoot("panel-menu", panel._tab_row.more_menu, more_button)
    panel._tab_row.more_menu.close()

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
