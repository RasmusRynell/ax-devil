"""Save offscreen screenshots of the main UI surfaces, for checking a UI change before and after.

The video surfaces show a generated clip with a small ONVIF metadata file, so the canvas has boxes and labels and the
media tools list entities and events. Runs inside the test suite's isolated home folder, so it never reads or changes
the user's settings, and never opens a window on the desktop. Text sizes switch live in one window per theme, as the
Settings dialog does::

    PYTHONPATH=. uv run pytest -p tests.conftest tools/ui_screenshots.py -q
    PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/after uv run pytest -p tests.conftest tools/ui_screenshots.py -q

Shots go to ``AX_DEVIL_UI_SHOTS`` (default ``/tmp/ax-devil-ui-shots``) as ``<theme>-<text size>-<surface>.png``.
Dialogs larger than a window size get an extra ``-wide`` or ``-narrow`` shot clamped to it. Each run first deletes
the theme's earlier shots there, so a folder reused across runs holds only current shots.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QTabWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.scene.inspection import build_entity_hover_html
from ax_devil.modules.scene.model import BoundingBox, Classification, Entity, EntityId, MotionState, Observation, Score
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    LiveStreamItem,
    OverlayFile,
    PlaylistItem,
    VideoItem,
    new_item_id,
)
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.content_browser import TREE_LABEL_COLUMN, TREE_ROW_ROLE
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML

OUT = Path(os.environ.get("AX_DEVIL_UI_SHOTS", "/tmp/ax-devil-ui-shots"))
WINDOW_SIZES = {"wide": (1440, 900), "narrow": (1000, 640)}
TEXT_SIZES = (TextSize.MEDIUM, TextSize.LARGER)
CLIP_FPS = 30
CLIP_FRAMES = 60
SHOWN_FRAME = 30
# Object id, class, first and last frame, start and end box (left, top, width, height as frame fractions), likelihood.
TRACKS = (
    ("1", "Human", 0, 59, (0.05, 0.45, 0.10, 0.40), (0.42, 0.40, 0.10, 0.42), 0.97),
    ("2", "Vehicle", 0, 59, (0.78, 0.25, 0.15, 0.30), (0.40, 0.30, 0.15, 0.30), 0.88),
    ("3", "Human", 0, 24, (0.30, 0.35, 0.12, 0.45), (0.45, 0.36, 0.12, 0.45), 0.42),
    ("7", "Human", 25, 59, (0.48, 0.36, 0.12, 0.45), (0.65, 0.38, 0.12, 0.45), 0.46),  # 3, renamed; overlaps 1 and 2
    ("4", "Head", 0, 59, (0.15, 0.07, 0.03, 0.05), (0.75, 0.08, 0.03, 0.05), 0.71),
    ("5", "Truck", 10, 59, (0.02, 0.04, 0.95, 0.92), (0.02, 0.04, 0.95, 0.92), 0.35),  # nearly the whole frame
    ("6", "Bicycle", 0, 19, (0.60, 0.70, 0.10, 0.12), (0.70, 0.70, 0.10, 0.12), 0.64),
    ("8", "Car", 0, 29, (0.85, 0.75, 0.12, 0.10), (0.95, 0.75, 0.12, 0.10), 0.81),
)
EVENTS = {
    20: '<tt:Delete ObjectId="6"/>',
    25: '<tt:Rename><tt:from ObjectId="3"/><tt:to ObjectId="7"/></tt:Rename>',
    SHOWN_FRAME: '<tt:Delete ObjectId="8"/>',
}


def _onvif_box(left: float, top: float, width: float, height: float) -> str:
    """Return an ONVIF bounding box, whose coordinates run from -1 to 1 with y pointing up."""
    return (
        f'<tt:BoundingBox left="{2 * left - 1:.4f}" top="{1 - 2 * top:.4f}" '
        f'right="{2 * (left + width) - 1:.4f}" bottom="{1 - 2 * (top + height):.4f}"/>'
    )


def _write_tracks(path: Path) -> Path:
    """Write an ONVIF metadata file for the generated clip, with tracks, deletes and a rename, and return its path."""
    lines = []
    for frame in range(CLIP_FRAMES):
        objects = []
        for object_id, class_name, first, last, start, end, likelihood in TRACKS:
            if first <= frame <= last:
                progress = (frame - first) / (last - first)
                left, top, width, height = (a + (b - a) * progress for a, b in zip(start, end))
                objects.append(
                    f'<tt:Object ObjectId="{object_id}"><tt:Appearance>'
                    f"<tt:Shape>{_onvif_box(left, top, width, height)}</tt:Shape>"
                    f'<tt:Class><tt:Type Likelihood="{likelihood}">{class_name}</tt:Type></tt:Class>'
                    "</tt:Appearance></tt:Object>"
                )
        tree = f"<tt:ObjectTree>{EVENTS[frame]}</tt:ObjectTree>" if frame in EVENTS else ""
        # Samples match frames by time, and the generated clip's frame times start at zero.
        time = datetime.fromtimestamp(frame / CLIP_FPS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        namespace = "http://www.onvif.org/ver10/schema"
        lines.append(f'<tt:Frame xmlns:tt="{namespace}" UtcTime="{time}">{"".join(objects)}{tree}</tt:Frame>')
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _add_and_open(qtbot: QtBot, session: WorkspaceSession, item: VideoItem) -> OfflineVideoViewerWidget | None:
    """Add *item* and return the viewer it opens once it has resolved in the background."""
    previous = session.focused_offline_viewer()
    session.add_items([item])
    qtbot.waitUntil(lambda: session.focused_offline_viewer() not in (None, previous), timeout=10000)
    return session.focused_offline_viewer()


def _show_tracked_frame(qtbot: QtBot, viewer: OfflineVideoViewerWidget | None) -> OfflineVideoViewerWidget:
    """Pause *viewer* on the frame that the tracks and events are built around, and return it."""
    assert viewer is not None
    # Entries open in the background, so the viewport appears after the content is loaded.
    qtbot.waitUntil(lambda: viewer.findChild(FrameViewport) is not None, timeout=10000)
    viewer.pause_playback()
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    viewer._jump_to_frame(SHOWN_FRAME)

    def shows_frame() -> bool:
        return viewport._video_frame is not None and viewport._video_frame.frame.frame_id == SHOWN_FRAME

    qtbot.waitUntil(shows_frame, timeout=10000)
    qtbot.wait(200)  # Let the media tools follow the new frame.
    return viewer


def _grab_next_dialog(name: str, before: Callable[[QDialog], None] | None = None) -> None:
    """Save and close the dialog that the next call opens modally, after running *before* on it."""

    def grab() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                if before is not None:
                    before(widget)
                    QApplication.processEvents()
                widget.grab().save(str(OUT / f"{name}.png"))
                natural = widget.size()
                for size_name, (width, height) in WINDOW_SIZES.items():
                    clamped = QSize(min(natural.width(), width), min(natural.height(), height))
                    if clamped == natural:
                        continue
                    widget.resize(clamped)
                    QApplication.processEvents()
                    widget.grab().save(str(OUT / f"{name}-{size_name}.png"))
                widget.reject()

    QTimer.singleShot(300, grab)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_screenshots(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    video_file_factory: Callable[[float, int], Path],
    theme: str,
) -> None:
    """Save the welcome screen, tracked video lanes with media tools, the catalog viewer and the main dialogs.

    Switches text size live.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob(f"{theme}-*.png"):
        stale.unlink()
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
    apply_theme(theme)
    # Change the text size through the setting, as the app does, so dialogs show the matching choice.
    GlobalSettings.reset_instance()
    settings = GlobalSettings()
    settings.text_size_changed.connect(lambda size: apply_text_size(size.body_px))
    apply_text_size(settings.text_size.body_px)

    shortcuts = ShortcutManager()
    shortcuts.register_defaults()
    window = MainWindow(shortcuts, render_catalog_manager, use_custom_frame=True)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # Recent folders show as ~/..., as on a real machine.
    recent = []
    for days_ago, name in ((0, "Parking lot"), (2, "Entrance cameras"), (40, "Exp 3")):
        path = tmp_path / "reviews" / f"{name}.ax-devil.workspace"
        path.parent.mkdir(exist_ok=True)
        path.touch()
        stamp = time.time() - days_ago * 86400
        os.utime(path, (stamp, stamp))
        recent.append(path)
    window._workspace_session._start_panel.set_recent_workspaces(recent)
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        window.resize(*WINDOW_SIZES["wide"])
        qtbot.wait(200)
        window.grab().save(str(OUT / f"{theme}-{text_size.value}-welcome.png"))

    catalog_viewer = CatalogViewerWindow(render_catalog_manager, use_custom_frame=True)
    qtbot.addWidget(catalog_viewer)
    catalog_viewer.show()
    qtbot.waitUntil(lambda: bool(catalog_viewer.sheet_titles()), timeout=10000)
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        catalog_viewer.resize(*WINDOW_SIZES["narrow"])
        qtbot.wait(500)
        catalog_viewer.grab().save(str(OUT / f"{theme}-{text_size.value}-catalog-viewer.png"))
    catalog_viewer.hide()

    session = window._workspace_session
    clip_path = video_file_factory(CLIP_FRAMES / CLIP_FPS, CLIP_FPS)
    tracked_clip = VideoItem(
        video=clip_path,
        overlays=(OverlayFile(_write_tracks(tmp_path / "tracks.xml"), ONVIF_XML),),
    )
    viewer = _show_tracked_frame(qtbot, _add_and_open(qtbot, session, tracked_clip))
    shortcuts.get_action("view.toggle_media_tools").trigger()
    media_tools_tabs = viewer.findChild(QTabWidget, "mediaToolsTabs")
    entities_page = viewer.findChild(QWidget, "mediaToolsEntities")
    events_page = viewer.findChild(QWidget, "mediaToolsEvents")
    assert media_tools_tabs is not None and entities_page is not None and events_page is not None
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        prefix = f"{theme}-{text_size.value}"
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{prefix}-video-{size_name}.png"))
        media_tools_tabs.setCurrentWidget(events_page)
        qtbot.wait(200)
        window.grab().save(str(OUT / f"{prefix}-video-events-narrow.png"))
        media_tools_tabs.setCurrentWidget(entities_page)
        for name, action_id in (
            ("settings", "app.settings"),
            ("shortcuts", "app.keyboard_shortcuts"),
            ("add-video", "app.add_video"),
            ("add-live-stream", "app.add_live_stream"),
            ("add-playlist", "app.add_playlist"),
        ):
            _grab_next_dialog(f"{prefix}-{name}")
            shortcuts.get_action(action_id).trigger()
        _grab_next_dialog(
            f"{prefix}-settings-storage", lambda dialog: cast(SettingsDialog, dialog)._tabs.setCurrentIndex(2)
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(
            f"{prefix}-settings-streams", lambda dialog: cast(SettingsDialog, dialog)._tabs.setCurrentIndex(1)
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(
            f"{prefix}-settings-manual-cache",
            lambda dialog: cast(SettingsDialog, dialog)._video_cache_mode.setCurrentIndex(1),
        )
        shortcuts.get_action("app.settings").trigger()
        _grab_next_dialog(f"{prefix}-quick-setup")
        window.show_quick_setup()
    # Quick Setup opens with room for the largest text size; picking it must not need scrolling.
    settings.text_size = TextSize.SMALL
    _grab_next_dialog(
        f"{theme}-quick-setup-small-to-larger",
        lambda dialog: cast(QuickSetupDialog, dialog).text_size_row.select(TextSize.LARGER),
    )
    window.show_quick_setup()

    viewer.set_pinned(True)  # An unpinned lane is a preview that the next video replaces.
    second_clip = tmp_path / "second-lane.mp4"
    shutil.copyfile(tracked_clip.video, second_clip)
    _show_tracked_frame(
        qtbot, _add_and_open(qtbot, session, replace(tracked_clip, id=new_item_id(), label="", video=second_clip))
    )
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{theme}-{text_size.value}-two-lanes-{size_name}.png"))

    # Live streams, videos, a playlist, and a missing video together. Adding opens the first live stream; its
    # documentation-only address (RFC 5737) never reaches a device.
    mixed_dir = tmp_path / "night-shift"
    videos_dir = mixed_dir / "videos"
    overlays_dir = mixed_dir / "overlays"
    videos_dir.mkdir(parents=True)
    overlays_dir.mkdir()
    for index in range(1, 6):
        shutil.copyfile(clip_path, videos_dir / f"run_{index:02d}.mp4")
        _write_tracks(overlays_dir / f"run_{index:02d}.xml")
    # The missing video makes adding report a modal warning, which would wait forever offscreen.
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    session.add_items(
        [
            LiveStreamItem(host="192.0.2.10", label="Entrance"),
            LiveStreamItem(host="192.0.2.11", camera_head=2, label="Parking north"),
            PlaylistItem(
                resolver="folder_pair",
                settings={"videos_dir": str(videos_dir), "overlays_dir": str(overlays_dir), "handler_type": ONVIF_XML},
                label="Night shift",
            ),
            VideoItem(video=tmp_path / "gate.mp4"),
        ]
    )

    def tree_items() -> list[QTreeWidgetItem]:
        """Return every row of the content browser, depth first."""
        iterator = QTreeWidgetItemIterator(session._content_browser._tree)
        items = []
        while (item := iterator.value()) is not None:
            items.append(item)
            iterator += 1
        return items

    def row_of(item: QTreeWidgetItem) -> WorkspaceBrowserRow:
        """Return the row an item shows."""
        return cast(WorkspaceBrowserRow, item.data(TREE_LABEL_COLUMN, TREE_ROW_ROLE))

    qtbot.waitUntil(lambda: all(row_of(item).icon_kind != "pending" for item in tree_items()), timeout=10000)
    for item in tree_items():
        item.setExpanded(item.isExpanded() or row_of(item).label == "Night shift")
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        window.resize(*WINDOW_SIZES["wide"])
        qtbot.wait(500)
        window.grab().save(str(OUT / f"{theme}-{text_size.value}-mixed-workspace.png"))


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_hover_screenshots(qtbot: QtBot, theme: str) -> None:
    """Save compact and scrolling object inspectors in wide and narrow viewers."""
    OUT.mkdir(parents=True, exist_ok=True)
    apply_theme(theme)
    entity = Entity(id=EntityId("12d872dd-1285-536d-bc40-123456789abc"), motion_state=MotionState.Unknown)
    entity.add_observation(
        Observation(
            geometry=BoundingBox.from_xywh(0.312, 0.142, 0.447, 0.847),
            classification=[Classification(type="vehicle_other", score=Score(0.68))],
            timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
            debug={
                "recentMotion": {
                    "trackConfident": True,
                    "moteOverlap": {"currentIou": 0, "iou": 0, "longLived": False},
                    "velocity": {"x": 0.04316, "y": -0.06882, "stdDevX": 0.344, "stdDevY": 0.5898},
                    "positionStability": {f"metric_{index}": index / 100 for index in range(30)},
                }
            },
        )
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.show()
    card = EntityHoverCard(parent)
    for text_size in TEXT_SIZES:
        apply_text_size(text_size.body_px)
        for size_name, size in (("wide", (1000, 640)), ("narrow", (320, 600))):
            parent.resize(*size)
            card.show_for(str(entity.id), build_entity_hover_html(entity), 40, 40, interactive=True)
            QApplication.processEvents()
            parent.grab().save(str(OUT / f"{theme}-{text_size.value}-hover-{size_name}.png"))
            card.hide_card()
            card.show_for("compact", "<b>Person</b><br/>ID: 12<br/>Confidence: 0.98", 40, 40, interactive=True)
            QApplication.processEvents()
            parent.grab().save(str(OUT / f"{theme}-{text_size.value}-hover-compact-{size_name}.png"))
            card.hide_card()
