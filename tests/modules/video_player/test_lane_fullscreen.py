"""Lane fullscreen preserves the existing display and its layout ownership."""

from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRect, Qt
from PySide6.QtGui import QAction, QImage, QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays
from ax_devil.modules.video_player.orchestration.fullscreen import LaneFullscreenController, _FullscreenWindow
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from tests.helpers.qt_events import make_wheel_event
from tests.helpers.shortcuts import make_shortcut_manager


def test_lane_shortcuts_restore_layout_and_keep_shared_actions(qtbot: QtBot) -> None:
    """F selects one lane, shared playback works, and Esc/F restore its exact slot."""
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    first, second = FrameDisplay(), FrameDisplay()
    layout.addWidget(first, 2)
    layout.addWidget(second, 3)
    manager = make_shortcut_manager()
    manager.install(window)
    toggle = manager.get_action("view.toggle_lane_fullscreen")
    playback = manager.get_action("playback.play_pause")
    controller = LaneFullscreenController(window, [toggle, playback])
    toggle.triggered.connect(controller.toggle)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(lambda: window.isActiveWindow() and QApplication.focusWindow() is window.windowHandle())
    for exit_key in (Qt.Key.Key_Escape, Qt.Key.Key_F):
        QTest.mouseClick(second.viewport, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(second.viewport.hasFocus)
        QTest.keyClick(second.viewport, Qt.Key.Key_F)
        host = second.window()
        qtbot.waitExposed(host)
        qtbot.waitUntil(host.isActiveWindow)
        qtbot.waitUntil(lambda: QApplication.focusWindow() is host.windowHandle())
        assert host.isFullScreen()
        assert first.window() is window
        assert second.viewport.parentWidget() is second
        with qtbot.waitSignal(playback.triggered):
            QTest.keyClick(second.viewport, Qt.Key.Key_Space)
        QTest.keyClick(second.viewport, exit_key)
        assert second.window() is window
        assert layout.indexOf(first) == 0
        assert layout.indexOf(second) == 1
        assert layout.stretch(1) == 3
        qtbot.waitUntil(lambda: window.isActiveWindow() and QApplication.focusWindow() is window.windowHandle())
    first.cleanup()
    second.cleanup()


def test_cleanup_and_window_close_restore_display(qtbot: QtBot) -> None:
    """Lane teardown and the window close action both release fullscreen safely."""
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    display = FrameDisplay()
    layout.addWidget(display)
    controller = LaneFullscreenController(window, [])
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    display.viewport.setFocus()
    controller.toggle()
    host = display.window()
    host.close()
    assert display.window() is window
    qtbot.waitUntil(window.isActiveWindow)
    display.viewport.setFocus()
    controller.toggle()
    qtbot.waitUntil(display.window().isFullScreen)
    display.cleanup()
    assert display.window() is window
    assert layout.indexOf(display) == 0
    controller.exit()


def test_typing_f_does_not_enter_fullscreen(qtbot: QtBot) -> None:
    """Editing a field inside a display does not trigger its single-letter action."""
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    display = FrameDisplay()
    layout.addWidget(display)
    editor = QLineEdit(display)
    editor.show()
    action = QAction(window)
    action.setShortcut("F")
    window.addAction(action)
    controller = LaneFullscreenController(window, [action])
    action.triggered.connect(controller.toggle)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    editor.setFocus()
    QTest.keyClicks(editor, "f")
    assert editor.text() == "f"
    assert display.window() is window
    display.cleanup()


def test_fullscreen_geometry_uses_target_monitor_before_first_show(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A monitor away from the origin must control initial and first-show geometry."""
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setGeometry(100, 100, 600, 400)
    screen = Mock(spec=QScreen)
    bounds = QRect(-1920, 0, 1920, 1080)
    screen.geometry.return_value = bounds
    set_screen = Mock()
    monkeypatch.setattr(_FullscreenWindow, "setScreen", set_screen)
    host = _FullscreenWindow(parent, cast(QScreen, screen))
    qtbot.addWidget(host)
    set_screen.assert_called_with(screen)
    assert host.geometry() == bounds
    # Exercise ChromeWindow's first-show hook without the single-monitor
    # offscreen platform remapping the synthetic monitor's native window.
    host.setGeometry(parent.geometry())
    host._prepare_geometry()
    assert host.geometry() == bounds


def test_zoom_in_fullscreen_matches_peer_after_escape(qtbot: QtBot) -> None:
    """Fullscreen interaction and layout restoration use the same view as sibling lanes."""
    window = QWidget()
    qtbot.addWidget(window)
    window.resize(1000, 400)
    layout = QHBoxLayout(window)
    first, second = FrameDisplay(), FrameDisplay()
    layout.addWidget(first)
    layout.addWidget(second)
    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(0)
    frame = VideoFrameWithOverlays(VideoFrame(image, 0.0, 0), None)
    first.display_frame(frame)
    second.display_frame(frame)
    first.viewportChanged.connect(second.set_viewport)
    second.viewportChanged.connect(first.set_viewport)
    controller = LaneFullscreenController(window, [])
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    first.viewport.setFocus()
    controller.toggle()
    qtbot.waitUntil(first.window().isActiveWindow)
    position = QPointF(first.viewport.width() * 0.7, first.viewport.height() * 0.4)
    for _ in range(6):
        first.viewport.wheelEvent(make_wheel_event(position, 120))
    base = first.viewport.frame_display_rect()
    assert base is not None
    expected = first.viewport.viewport_state.to_normalized(base)
    QTest.keyClick(first.viewport, Qt.Key.Key_Escape)
    qtbot.waitUntil(window.isActiveWindow)
    QCoreApplication.processEvents()
    for display in (first, second):
        base = display.viewport.frame_display_rect()
        assert base is not None
        assert tuple(display.viewport.viewport_state.to_normalized(base)) == pytest.approx(tuple(expected))
        display.cleanup()
