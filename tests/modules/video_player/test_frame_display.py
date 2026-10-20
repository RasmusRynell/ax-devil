"""Tests for the public FrameDisplay surface."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.appearance import notify_text_size_applied
from ax_devil.modules.video_player.constants import VIDEO_MIN_WIDTH
from ax_devil.modules.video_player.ui.draggable import DraggableHandle, DraggablePanel, panel_width
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition


def test_mounted_overlay_is_shown_inside_the_video_area(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)
    display.resize(320, 240)
    display.show()

    overlay = QLabel("caller-owned", display.viewport)  # Created on the viewport, as the viewers do.
    display.mount_overlay(overlay, position=OverlayPosition.BOTTOM_CENTER)
    overlay.show()  # A plain overlay's visibility stays with its caller.
    QCoreApplication.processEvents()

    assert overlay.isVisible()
    viewport = display.viewport
    assert viewport.rect().contains(QRect(overlay.mapTo(viewport, QPoint(0, 0)), overlay.size()))


def test_viewport_gets_initial_focus_before_side_panel_text_fields(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    layout = QHBoxLayout(window)
    display = FrameDisplay()
    search = QLineEdit()
    layout.addWidget(display)
    layout.addWidget(search)

    window.show()
    window.activateWindow()

    qtbot.waitUntil(window.isActiveWindow)
    assert window.focusWidget() is display.viewport


def test_info_overlay_temporarily_hides_opted_in_mounted_widget(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)
    lane_name = QLabel("DPM")
    persistent_status = QLabel("warning")
    display.mount_overlay(
        lane_name,
        position=OverlayPosition.TOP_LEFT,
        hide_while_info_shown=True,
    )
    display.mount_overlay(persistent_status, position=OverlayPosition.TOP_RIGHT)

    display.viewport.toggle_info_overlay()

    assert lane_name.isHidden()
    assert not persistent_status.isHidden()

    display.viewport.toggle_info_overlay()

    assert not lane_name.isHidden()


def test_frame_display_side_panel_mount_replaces_caller_content(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)

    first = QLabel("first")
    replacement = QLabel("replacement")
    display.enable_side_panel(first)
    display.set_side_panel_widget(replacement)
    QCoreApplication.processEvents()

    assert first.parent() is None
    assert replacement in display.findChildren(QLabel)


def test_cleanup_releases_mounted_overlays_and_side_panel(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)

    viewport = display.viewport
    overlay = QLabel("caller-owned")
    display.mount_overlay(overlay, position=OverlayPosition.BOTTOM_CENTER)
    content = QLabel("caller content")
    display.enable_side_panel(content)
    QCoreApplication.processEvents()

    assert display.findChildren(DraggablePanel)
    assert viewport.findChildren(DraggableHandle)

    display.cleanup()
    QCoreApplication.processEvents()

    assert overlay not in viewport.findChildren(QLabel)
    assert display.findChildren(DraggablePanel) == []
    assert viewport.findChildren(DraggableHandle) == []
    assert content.parent() is None


def test_side_panel_hides_its_content_while_collapsed(qtbot: QtBot) -> None:
    panel = DraggablePanel(animation_duration_ms=1)
    qtbot.addWidget(panel)
    content = QWidget()
    panel.set_content_widget(content)
    panel.show()
    assert content.isHidden()

    panel.expand()
    assert not content.isHidden()

    panel.collapse()
    assert not content.isHidden()  # Still visible while the collapse animates.
    qtbot.waitUntil(content.isHidden)
    panel.expand()
    panel.collapse()
    with qtbot.waitSignals([panel.animation.finished, panel.min_animation.finished]):
        panel.expand()
    assert not content.isHidden()
    assert panel.width() == panel.expanded_width


def test_dragging_side_panel_open_to_a_width_and_closed_updates_content_visibility(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 400)
    layout = QHBoxLayout(host)
    handle = DraggableHandle(host)
    panel = DraggablePanel(host, animation_duration_ms=1)
    layout.addWidget(handle)
    layout.addStretch()
    layout.addWidget(panel)
    handle.setTarget(panel)
    content = QLabel("Panel content")
    panel.set_content_widget(content)
    host.show()
    QCoreApplication.processEvents()

    try:
        for width in (300, 420):  # The release keeps the dragged width instead of snapping to a fixed one.
            start = handle.rect().center()
            opened = QPoint(start.x() - width, start.y())
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, opened)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=opened)
            qtbot.waitUntil(content.isVisible)
            assert panel.width() == width

            closed = QPoint(start.x() + width, start.y())
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, closed)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=closed)
            qtbot.waitUntil(lambda: content.isHidden() and panel.width() == panel.collapsed_width)
    finally:
        handle.cleanup()
        panel.cleanup()


def _display_in_window(qtbot: QtBot, width: int) -> tuple[QWidget, FrameDisplay, DraggablePanel, QLabel]:
    window = QWidget()
    qtbot.addWidget(window)
    layout = QHBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    display = FrameDisplay()
    layout.addWidget(display)
    content = QLabel("tools")
    display.enable_side_panel(content)
    window.resize(width, 400)
    window.show()
    panel = display.findChild(DraggablePanel)
    assert panel is not None
    return window, display, panel, content


@pytest.mark.parametrize(
    ("pane", "wanted", "content_min", "expected"),
    [
        (1000, 400, 200, 400),  # Fits: the wanted width.
        (1000, 100, 200, 200),  # Never below the content minimum.
        (1000, 900, 200, 1000 - VIDEO_MIN_WIDTH),  # The video keeps its minimum before the panel grows.
        (300, 400, 200, 200),  # The video gives way before the panel drops below its content.
        (150, 400, 200, 150),  # Never wider than the pane.
    ],
)
def test_panel_width_keeps_content_whole_and_the_video_next(
    pane: int, wanted: int, content_min: int, expected: int
) -> None:
    assert panel_width(pane=pane, wanted=wanted, content_min=content_min, video_min=VIDEO_MIN_WIDTH) == expected


def test_dragged_side_panel_width_survives_close_open_and_pane_resize(qtbot: QtBot) -> None:
    """The width the user dragged to is kept for the session; a narrow pane only limits it for a while."""
    window, display, panel, content = _display_in_window(qtbot, 1200)
    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)
    wanted = panel.width() + 80
    panel.drag_to(10)
    assert panel.width() == panel._content_minimum_width() > 10  # A drag never takes the panel below its content.
    panel.drag_to(wanted)
    panel.end_drag()
    assert panel.width() == wanted

    display.set_side_panel_open(False)
    qtbot.waitUntil(content.isHidden)
    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == wanted)

    window.resize(wanted + 20, 400)
    qtbot.waitUntil(lambda: panel.width() == wanted + 20 - VIDEO_MIN_WIDTH)
    assert display.is_side_panel_open()
    assert content.isVisible()

    window.resize(1200, 400)
    qtbot.waitUntil(lambda: panel.width() == wanted)


def test_side_panel_opening_during_resize_keeps_content_and_video_visible(qtbot: QtBot) -> None:
    window, display, panel, content = _display_in_window(qtbot, 1200)

    with qtbot.waitSignals([panel.animation.finished, panel.min_animation.finished]):
        display.set_side_panel_open(True)
        window.resize(300, 400)
        resized_size = window.size()

    qtbot.waitUntil(
        lambda: (
            display.rect().contains(panel.geometry())
            and display.rect().contains(display.viewport.geometry())
            and not panel.geometry().intersects(display.viewport.geometry())
        )
    )
    assert window.size() == resized_size
    assert display.is_side_panel_open()
    assert content.isVisible()
    assert panel.rect().contains(content.geometry())
    assert content.width() >= content.minimumSizeHint().width()
    assert display.viewport.isVisible()
    assert not display.viewport.size().isEmpty()


def test_side_panel_width_ignores_content_changes_until_the_text_size_is_applied(qtbot: QtBot) -> None:
    """Content that changes size does not move the panel; a text-size change refits it to the content."""
    _window, display, panel, content = _display_in_window(qtbot, 1200)
    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)
    width = panel.width()

    content.setMinimumWidth(620)
    QCoreApplication.processEvents()
    assert panel.width() == width

    notify_text_size_applied()
    qtbot.waitUntil(lambda: panel.width() >= 620)
    assert display.is_side_panel_open()


def test_side_panel_enabled_on_a_shown_display_takes_half_its_width(qtbot: QtBot) -> None:
    """A panel added after the display has its size still takes at most half of it when opened."""
    display = FrameDisplay()
    qtbot.addWidget(display)
    display.resize(640, 400)
    display.show()
    QCoreApplication.processEvents()

    display.enable_side_panel(QLabel("tools"))
    display.set_side_panel_open(True)
    panel = display.findChild(DraggablePanel)
    assert panel is not None
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)
    assert 0 < panel.width() <= display.width() // 2
