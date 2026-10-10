"""Offline playback through the workspace with real decoding and worker teardown."""

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QSurface
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.main_window import MainWindow
from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.export.encoder import VideoEncoder
from ax_devil.modules.workspace.core import VideoItem


@pytest.mark.parametrize("use_custom_frame", [False, True])
def test_workspace_opens_seeks_and_closes_real_video(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
    use_custom_frame: bool,
) -> None:
    """Fullscreen shortcuts reach real playback and removing content stops its worker."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
    path = tmp_path / "clip.mp4"
    encoder = VideoEncoder(path, width=32, height=24, fps=1)
    for index, color in enumerate(("red", "green", "blue")):
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        encoder.write_frame(image, timestamp_us=index * 1_000_000)
    encoder.finish()

    shortcuts = ShortcutManager()
    shortcuts.register_defaults()
    window = MainWindow(shortcuts, render_catalog_manager, use_custom_frame=use_custom_frame)
    qtbot.addWidget(window)
    session = window._workspace_session
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    native_id = window.internalWinId()
    try:
        session.add_items([VideoItem(video=path)])
        viewer = session.focused_offline_viewer()
        assert viewer is not None
        qtbot.waitUntil(lambda: viewer._runtime is not None)
        viewer.pause_playback()
        viewport = viewer.findChild(FrameViewport)
        assert viewport is not None
        qtbot.waitUntil(lambda: viewport._video_frame is not None)
        assert window.internalWinId() == native_id
        qtbot.waitUntil(window.isActiveWindow)

        with SettingsDialog(window) as dialog:
            dialog.show()
            handle = dialog.windowHandle()
            assert handle is not None
            assert handle.surfaceType() == QSurface.SurfaceType.RasterSurface
            dialog.reject()
        # PySide6 before 6.11.2 invalidates the menu wrapper once its QAction wrapper is collected.
        file_action = window.menu_host().actions()[0]
        menu = cast(QMenu | None, file_action.menu())
        assert menu is not None
        menu.popup(window.mapToGlobal(window.rect().center()))
        handle = menu.windowHandle()
        assert handle is not None
        assert handle.surfaceType() == QSurface.SurfaceType.RasterSurface
        menu.hide()

        assert viewer._runtime is not None
        source = viewer._runtime.get_primary_video_source()
        assert source is not None
        delivery = source._frame_delivery
        assert delivery is not None

        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton)
        QTest.keyClick(viewport, Qt.Key.Key_F)
        fullscreen = viewport.window()
        qtbot.waitUntil(fullscreen.isActiveWindow)
        assert fullscreen.isFullScreen()
        with qtbot.waitSignal(viewer._runtime.playbackStateChanged) as started:
            QTest.keyClick(viewport, Qt.Key.Key_Space)
        assert started.args == [True]
        with qtbot.waitSignal(viewer._runtime.playbackStateChanged) as paused:
            QTest.keyClick(viewport, Qt.Key.Key_Space)
        assert paused.args == [False]
        QTest.keyClick(viewport, Qt.Key.Key_Right)
        qtbot.waitUntil(lambda: viewport._video_frame is not None and viewport._video_frame.frame.frame_id == 2)
        assert viewport._video_frame is not None
        pixel = viewport._video_frame.frame.image.pixelColor(16, 12)
        assert pixel.blue() > 240
        assert pixel.red() < 15
        assert pixel.green() < 15

        (item,) = session._workspace_store.workspace.items
        session._workspace_store.remove_item(item.id)

        assert session.focused_widget() is None
        assert window._lane_fullscreen._host is None
        assert delivery.wait(0)
        assert window.internalWinId() == native_id

        session.add_items([VideoItem(video=path)])
        reopened_viewer = session.focused_offline_viewer()
        assert reopened_viewer is not None
        qtbot.waitUntil(lambda: reopened_viewer._runtime is not None)
        reopened_viewport = reopened_viewer.findChild(FrameViewport)
        assert reopened_viewport is not None
        qtbot.waitUntil(lambda: reopened_viewport._video_frame is not None)
        assert window.internalWinId() == native_id
        qtbot.waitUntil(window.isActiveWindow)
    finally:
        session.cleanup()
