"""Save offscreen screenshots of the main UI surfaces, for checking a UI change before and after.

The video surfaces show a generated clip with a small ONVIF metadata file, so the canvas has boxes and labels and the
media tools list entities and events. Runs inside the test suite's isolated home folder, so it never reads or changes
the user's settings, and never opens a window on the desktop. Text sizes switch live in one window per theme, as the
Settings dialog does::

    PYTHONPATH=. uv run pytest -p tests.conftest tools/ui_screenshots.py -q
    PYTHONPATH=. AX_DEVIL_UI_SHOTS=/tmp/after uv run pytest -p tests.conftest tools/ui_screenshots.py -q

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
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.app import Application
from ax_devil.modules.application_shell.quick_setup_dialog import QuickSetupDialog
from ax_devil.modules.catalog_viewer import CatalogViewerWindow
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, SignIn
from ax_devil.modules.plugin_system import DECODER_PLUGIN_TYPE, RuntimePluginRegistry
from ax_devil.modules.scene.inspection import build_entity_hover_html
from ax_devil.modules.scene.model import BoundingBox, Classification, Entity, EntityId, MotionState, Observation, Score
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.video_player.ui.entity_hover_card import EntityHoverCard
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.section_tree import ROW_ROLE
from tools.ui_samples import CLIP_FPS, CLIP_FRAMES, SHOWN_FRAME, sample_workspace

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
TEXT_SIZES = (TextSize.MEDIUM, TextSize.LARGER)


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
    config = ConfigManager()
    config.set("ui", {"theme": theme, "text_size": "medium", "quick_setup_done": True})
    config.set("settings", {"network_scan": {"on_startup": False, "sign_in": False}})
    config.save()
    GlobalSettings.reset_instance()
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
