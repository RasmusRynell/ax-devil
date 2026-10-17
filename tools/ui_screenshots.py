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

import atexit
import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QMenu,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.app import Application
from ax_devil.core.cancellation import CancelToken
from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLUGIN_API_VERSION,
    PlaylistResolverPlugin,
    PlaylistResolverWidget,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.video_player.engine.data_types import HoverHit
from ax_devil.modules.video_player.ui.hover_chip import HoverChip
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.media_tools.objects_page import ObjectScope
from ax_devil.modules.video_viewer.media_tools.page_stack import EventPageKey
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    OverlayFile,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    SeekableVideoContent,
    VideoItem,
    Workspace,
    create_entry_lane,
    save_workspace,
)
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML
from tools.ui_samples import CLIP_FPS, CLIP_FRAMES, SHOWN_FRAME, sample_workspace, write_tracks

OUT = Path(os.environ.get("AX_DEVIL_UI_SHOTS", "/tmp/ax-devil-ui-shots"))


def _shot_scale() -> float:
    """Return the device pixels per logical pixel chosen with ``AX_DEVIL_UI_SHOTS_SCALE``, 2 by default."""
    value = os.environ.get("AX_DEVIL_UI_SHOTS_SCALE", "2")
    try:
        scale = float(value)
    except ValueError:
        scale = math.nan
    if not (math.isfinite(scale) and scale > 0):
        raise pytest.UsageError(f"AX_DEVIL_UI_SHOTS_SCALE must be a positive number such as 1 or 2, not {value!r}")
    return scale


SCALE = _shot_scale()
# Qt reads these when the application starts, which pytest-qt does after collecting this module. Dialogs fit
# themselves to the screen, so the offscreen screen keeps its default 800x800 logical size at every scale.
os.environ["QT_SCALE_FACTOR"] = str(SCALE)
# Qt multiplies these into the scale, or turns it off, when the calling shell sets them.
for _inherited in ("QT_SCREEN_SCALE_FACTORS", "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_ENABLE_HIGHDPI_SCALING"):
    os.environ.pop(_inherited, None)
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    # One file per run, so overlapping runs with different scales never read each other's screen.
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="ax-devil-ui-shots-", delete=False) as config:
        json.dump({"screens": [{"name": "shots", "width": round(800 * SCALE), "height": round(800 * SCALE)}]}, config)
    _SCREEN_CONFIG = Path(config.name)
    atexit.register(_SCREEN_CONFIG.unlink, missing_ok=True)
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_SCREEN_CONFIG}"
WINDOW_SIZES = {"wide": (1440, 900), "narrow": (1000, 640)}
GRID_SIZE = (1920, 1080)
TEXT_SIZES = (TextSize.MEDIUM, TextSize.LARGER)


def _configured(theme: str) -> ConfigManager:
    """Save a configuration with *theme* and no start-up dialogs or network scan, and return it."""
    config = ConfigManager()
    config.set("ui", {"theme": theme, "text_size": "medium", "quick_setup_done": True})
    config.set("settings", {"network_scan": {"on_startup": False, "sign_in": False}})
    config.save()
    GlobalSettings.reset_instance()
    return config


def _trigger(window: QWidget, title: str) -> None:
    [action] = [action for action in window.actions() if action.text() == title]
    assert action.isEnabled(), title
    action.trigger()


def _select_tab(dialog: QWidget, title: str) -> None:
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    [index] = [index for index in range(tabs.count()) if tabs.tabText(index) == title]
    QTest.mouseClick(tabs.tabBar(), Qt.MouseButton.LeftButton, pos=tabs.tabBar().tabRect(index).center())


def _select_combo(dialog: QWidget, label: str) -> None:
    [combo] = [combo for combo in dialog.findChildren(QComboBox) if combo.findText(label) >= 0]
    combo.setCurrentText(label)


def _tree_row(window: QWidget, label: str) -> tuple[QTreeWidget, QTreeWidgetItem] | None:
    for tree in window.findChildren(QTreeWidget):
        if not tree.isVisible():
            continue
        iterator = QTreeWidgetItemIterator(tree)
        while (item := iterator.value()) is not None:
            text = item.data(0, Qt.ItemDataRole.AccessibleTextRole) or ""
            if text.split(", ", 1)[0] == label:
                return tree, item
            iterator += 1
    return None


def _open_video(qtbot: QtBot, window: QWidget, label: str) -> OfflineVideoViewerWidget:
    previous = set(window.findChildren(OfflineVideoViewerWidget))

    def can_open() -> bool:
        row = _tree_row(window, label)
        if row is None:
            return False
        model = cast(WorkspaceBrowserRow, row[1].data(0, ROW_ROLE))
        offered = model.offered(RowAction.OPEN)
        return offered is not None and not offered.disabled_reason

    qtbot.waitUntil(can_open, timeout=10000)
    row = _tree_row(window, label)
    assert row is not None
    tree, item = row
    tree.scrollToItem(item)
    tree.setCurrentItem(item)
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: bool(set(window.findChildren(OfflineVideoViewerWidget)) - previous), timeout=10000)
    [viewer] = set(window.findChildren(OfflineVideoViewerWidget)) - previous
    qtbot.waitUntil(lambda: viewer.findChild(FrameViewport) is not None, timeout=10000)
    viewer.pause_playback()
    [frame] = [spin for spin in viewer.findChildren(QSpinBox) if spin.toolTip() == "Frame"]
    frame.selectAll()
    QTest.keyClicks(frame, str(SHOWN_FRAME))
    QTest.keyClick(frame, Qt.Key.Key_Return)
    viewport = viewer.findChild(FrameViewport)
    assert viewport is not None
    # Observe decoded frame identity; readiness must mean the requested picture, not only the spinbox value.
    qtbot.waitUntil(
        lambda: viewport._video_frame is not None and viewport._video_frame.frame.frame_id == SHOWN_FRAME,
        timeout=10000,
    )
    return viewer


def _grab_next_dialog(name: str, before: Callable[[QDialog], None] | None = None) -> None:
    """Save and close the dialog that the next call opens modally, after running *before* on it."""

    def grab() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                try:
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
                finally:
                    widget.reject()

    QTimer.singleShot(300, grab)


def _fill_sidebar_views(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: QWidget) -> None:
    """Supply external scan results and a failed plugin; let the real views handle them."""
    cameras = [
        DiscoveredCamera(
            "192.0.2.34",
            "AXIS P3265-LVE",
            "ACCC8E123456",
            "12.6.85",
            "Dome Camera",
            93_600.0,
            "aarch64",
            "Ambarella CV25",
            SignIn.SIGNED_IN,
        ),
        DiscoveredCamera("192.0.2.51", "AXIS M3086-V", "", "11.11.73", "Dome Camera", sign_in=SignIn.REFUSED),
        DiscoveredCamera("192.0.2.77", "AXIS C1310-E", "", "12.2.40", "Network Speaker", sign_in=SignIn.REFUSED),
    ]
    monkeypatch.setattr("ax_devil.modules.workspace.ui.live_view_controller.scan_network", lambda **_kwargs: cameras)
    _show_sidebar(window, "Live")
    [ip_range] = [field for field in window.findChildren(QLineEdit) if field.placeholderText().startswith("IP range")]
    ip_range.setText("192.0.2.0/24")
    [scan] = [button for button in window.findChildren(QAbstractButton) if button.text() == "Rescan"]
    QTest.mouseClick(scan, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: _tree_row(window, "192.0.2.34") is not None, timeout=10000)

    # The registry is restored when the test ends.
    monkeypatch.setattr(
        RuntimePluginRegistry,
        "_plugins_by_type",
        {k: dict(v) for k, v in RuntimePluginRegistry._plugins_by_type.items()},
    )
    RuntimePluginRegistry.mark_failed(
        DECODER_PLUGIN_TYPE,
        "old-model-decoder",
        "old-model-decoder",
        "Plugin requires API 1, but this app has API 2",
    )


def _show_sidebar(window: QWidget, title: str) -> None:
    bar = window.findChild(QWidget, "AxDevilActivityBar")
    assert bar is not None
    [button] = [button for button in bar.findChildren(QAbstractButton) if button.toolTip().split(":", 1)[0] == title]
    if not button.isChecked():
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)


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
    config = _configured(theme)
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

    _trigger(window, "Render Catalogs")
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
    viewer = _open_video(qtbot, window, "Tracked video")
    _trigger(window, "Media Tools Panel")
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
            _grab_next_dialog(f"{prefix}-{name}")
            _trigger(window, action_id)
        _grab_next_dialog(f"{prefix}-settings-storage", lambda dialog: _select_tab(dialog, "Storage"))
        _trigger(window, "Settings")
        _grab_next_dialog(f"{prefix}-settings-streams", lambda dialog: _select_tab(dialog, "Stream defaults"))
        _trigger(window, "Settings")
        _grab_next_dialog(f"{prefix}-settings-network-scan", lambda dialog: _select_tab(dialog, "Network scan"))
        _trigger(window, "Settings")
        _grab_next_dialog(
            f"{prefix}-settings-manual-cache",
            lambda dialog: _select_combo(dialog, "Manual"),
        )
        _trigger(window, "Settings")
        _grab_next_dialog(f"{prefix}-quick-setup")
        _trigger(window, "Quick Setup")
    # Quick Setup opens with room for the largest text size; picking it must not need scrolling.
    settings.text_size = TextSize.SMALL
    _grab_next_dialog(
        f"{theme}-quick-setup-small-to-larger",
        lambda dialog: cast(QuickSetupDialog, dialog).text_size_row.select(TextSize.LARGER),
    )
    _trigger(window, "Quick Setup")

    [pin] = [button for button in viewer.findChildren(QAbstractButton) if button.toolTip().startswith("Pin:")]
    QTest.mouseClick(pin, Qt.MouseButton.LeftButton)
    _open_video(qtbot, window, "Second lane")
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            qtbot.wait(500)
            window.grab().save(str(OUT / f"{theme}-{text_size.value}-two-lanes-{size_name}.png"))

    row = _tree_row(window, "Night shift")
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
    _fill_sidebar_views(qtbot, monkeypatch, window)
    for text_size in TEXT_SIZES:
        settings.text_size = text_size
        for size_name, size in WINDOW_SIZES.items():
            window.resize(*size)
            suffix = "" if size_name == "wide" else f"-{size_name}"
            for view_id in ("live", "offline", "bookmarks", "plugins"):
                _show_sidebar(window, view_id.capitalize())
                qtbot.wait(500)
                window.grab().save(str(OUT / f"{theme}-{text_size.value}-view-{view_id}{suffix}.png"))


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


def _shoot(window: QWidget, theme: str, name: str, menu: QMenu | None = None, anchor: QWidget | None = None) -> None:
    """Save *window* as ``<theme>-<name>.png``.

    An open *menu* is painted in below *anchor*, right-aligned, because popups are windows of their own and the
    offscreen screen keeps them inside its 800x800 area.
    """
    shot = window.grab()
    if menu is not None and anchor is not None:
        painter = QPainter(shot)
        corner = anchor.mapTo(window, QPoint(anchor.width() - menu.width(), anchor.height()))
        painter.drawPixmap(corner, menu.grab())
        painter.end()
    shot.save(str(OUT / f"{theme}-{name}.png"))


def _with_merge_and_split(panel: MediaToolsPanel) -> FrameEvent:
    """Add to *panel*'s history the events no decoder yields: a Merge on the Rename's frame and a later Split.

    Return the Rename event.
    """
    history = panel._history
    assert history is not None
    times = history.objects[0].frame_times_us

    def at(frame: int, kind: str, label: str, *entity_ids: str) -> FrameEvent:
        return FrameEvent(
            FrameIdentifier(sequence_id=frame, timestamp_monotime_us=times[frame]), kind, label, entity_ids
        )

    rename = next(event for event in history.events if event.kind == "Rename")
    merge = at(rename.frame_id.sequence_id, "Merge", "Merge 1, 7 → 1", "1", "7")
    split = at(45, "Split", "Split 2 → 2, 4", "2", "4")
    panel.set_scene_history(
        SceneHistory(events=(*history.events, merge, split), objects=history.objects, frame_count=history.frame_count)
    )
    return rename


def _object_point(viewport: FrameViewport, entity_id: str) -> QPoint:
    """Return the widget position of the centre of object *entity_id*'s box, where a hit test finds that object."""
    provider = viewport._interaction_provider()
    target = viewport._hover_target_rect()
    assert provider is not None and target is not None
    hit = provider.get_hit_by_id(entity_id)
    assert hit is not None
    x, y, width, height = hit.bounds
    centre = QPointF(target.x() + (x + width / 2) * target.width(), target.y() + (y + height / 2) * target.height())
    found = viewport._hit_test((centre.x() - target.x()) / target.width(), (centre.y() - target.y()) / target.height())
    assert found is not None and found.target_id == entity_id
    return centre.toPoint()


def _move_pointer(viewport: FrameViewport, pos: QPoint) -> None:
    """Send *viewport* a mouse move to *pos*, as the pointer would, without touching any real input device."""
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)


def _click_object(viewport: FrameViewport, entity_id: str) -> None:
    """Click object *entity_id* on *viewport*, then move the pointer onto empty video so the hover chip is gone."""
    pos = _object_point(viewport, entity_id)
    _move_pointer(viewport, pos)
    cast(Any, QTest).mouseClick(viewport, Qt.MouseButton.LeftButton, pos=pos)
    _move_pointer(viewport, QPoint(viewport.width() - 4, viewport.height() - 4))


def _grid_panels(qtbot: QtBot, viewer: OfflineVideoViewerWidget) -> list[MediaToolsPanel]:
    """Open the side panel of every lane of *viewer* and return the panels."""
    runtime = viewer._runtime
    assert runtime is not None
    for display in runtime.displays:
        display.set_side_panel_open(True)
    qtbot.wait(800)
    panels: list[MediaToolsPanel] = viewer.findChildren(MediaToolsPanel)
    assert len(panels) == len(runtime.displays)
    return panels


class _FourCameras(PlaylistResolverPlugin):
    """A playlist of one entry whose four lanes show copies of one clip, each with the same tracks."""

    @classmethod
    def plugin_id(cls) -> str:
        return "four_cameras"

    @classmethod
    def required_api_version(cls) -> int:
        return PLUGIN_API_VERSION

    @classmethod
    def display_name(cls) -> str:
        return "Four cameras"

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return the playlist of the clips in ``settings["videos"]``, all with the overlay ``settings["overlay"]``."""
        overlay = OverlayContent("tracks", FileOverlaySourceSpec(Path(str(settings["overlay"])), ONVIF_XML))
        lanes = tuple(
            create_entry_lane(
                SeekableVideoContent(display_name=Path(video).name, source_spec=FileVideoSourceSpec(Path(video))),
                default_considered=True,
                overlay=overlay,
            )
            for video in cast(list[str], settings["videos"])
        )
        return [PlaylistContent(display_name="Four cameras", entries=(PlaylistEntry(lanes, default_considered=True),))]

    def create_settings_widget(self) -> PlaylistResolverWidget:
        raise NotImplementedError("The screenshots only open a saved playlist.")


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
    config = _configured(theme)
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
                    resolver=_FourCameras.plugin_id(),
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
    RuntimePluginRegistry.register_plugin(_FourCameras, None)
    window = Application(config_path=config.config_path, workspace_file=workspace_path).start(qapp)
    qtbot.addWidget(window)
    qtbot.wait(100)  # Let the window restore its remembered size before choosing ours.

    def shoot(name: str, menu: QMenu | None = None, anchor: QWidget | None = None) -> None:
        qtbot.wait(300)  # Let the query debounce and the lists catch up.
        _shoot(window, theme, name, menu, anchor)

    # One video: the SHOWN_FRAME has objects 1, 2, 4, 5, 7; object 6 ended earlier and 8 ends one frame before.
    window.resize(*WINDOW_SIZES["wide"])
    viewer = _open_video(qtbot, window, "Tracked video")
    _trigger(window, "Media Tools Panel")
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
    _move_pointer(viewport, _object_point(viewport, "1"))
    shoot("hover-chip")
    _click_object(viewport, "1")
    shoot("selected-object")
    panel._stack.show_root(objects)
    panel.show_object("7")
    shoot("panel-object")
    panel.show_object("6")
    shoot("panel-object-absent")
    panel._stack.show_root(objects)
    qtbot.wait(200)
    panel._more_menu.popup(panel._more_button.mapToGlobal(QPoint(0, panel._more_button.height())))
    qtbot.wait(200)
    shoot("panel-menu", panel._more_menu, panel._more_button)
    panel._more_menu.close()

    # The Events tab: past rows above the now line at SHOWN_FRAME, upcoming ones below it.
    rename = _with_merge_and_split(panel)
    panel._tabs.setCurrentIndex(1)
    shoot("panel-events")
    events = panel.events_page
    events._query_bar.set_text("kind:merge")
    shoot("panel-events-query")
    events._query_bar.set_text("")
    events.eventActivated.emit(EventPageKey(rename))
    shoot("panel-event")

    # Four lanes of the same clip, the panel open on Objects in each, then an object page in each.
    window.resize(*GRID_SIZE)
    grid = _open_video(qtbot, window, "Four cameras")
    qtbot.waitUntil(lambda: len(grid.findChildren(FrameViewport)) == len(cameras), timeout=10000)
    qtbot.wait(1500)
    panels = _grid_panels(qtbot, grid)
    shoot("grid")
    for lane_panel in panels:
        _with_merge_and_split(lane_panel)
        lane_panel._tabs.setCurrentIndex(1)
    shoot("grid-events")
    for lane_panel in panels:
        lane_panel._tabs.setCurrentIndex(0)
    selected_lane = grid.findChildren(FrameViewport)[1]
    _click_object(selected_lane, "2")
    shoot("grid-selected")
    for lane_panel in panels:
        lane_panel._stack.show_root(lane_panel.objects_page)
    for lane_panel in panels:
        lane_panel.show_object("7")
    qtbot.wait(500)
    shoot("grid-object")
