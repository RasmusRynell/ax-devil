"""Tests for the public FrameDisplay surface."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.draggable import DraggableHandle, DraggablePanel
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition
from ax_devil.modules.video_player.ui.viewport import FrameViewport


def test_frame_display_mounts_overlay_widgets_through_public_interface(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)
    display.resize(320, 240)
    display.show()

    overlay = QLabel("caller-owned")
    display.mount_overlay(overlay, position=OverlayPosition.BOTTOM_CENTER)
    QCoreApplication.processEvents()

    assert overlay.parent() is display.viewport
    assert overlay in display.viewport.findChildren(QLabel)


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
        hide_while_inspecting=True,
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


def test_frame_display_cleanup_clears_viewport_state(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)

    viewport = display.viewport
    assert isinstance(viewport, FrameViewport)

    overlay = QLabel("caller-owned")
    display.mount_overlay(overlay, position=OverlayPosition.BOTTOM_CENTER)
    display.cleanup()

    assert overlay not in viewport.findChildren(QLabel)


def test_frame_display_cleanup_tears_down_side_panel(qtbot: QtBot) -> None:
    display = FrameDisplay()
    qtbot.addWidget(display)

    content = QLabel("caller content")
    display.enable_side_panel()
    display.set_side_panel_widget(content)
    QCoreApplication.processEvents()

    assert display.findChildren(DraggablePanel)
    assert display.viewport.findChildren(DraggableHandle)
    assert content.parent() is not None

    display.cleanup()
    QCoreApplication.processEvents()

    assert display.findChildren(DraggablePanel) == []
    assert display.viewport.findChildren(DraggableHandle) == []
    assert content.parent() is None

    placeholder_display = FrameDisplay()
    qtbot.addWidget(placeholder_display)
    placeholder_display.enable_side_panel()
    placeholder_display.set_side_panel_widget(None)
    QCoreApplication.processEvents()

    placeholder = next(label for label in placeholder_display.findChildren(QLabel) if label.text() == "No content")
    assert placeholder.parent() is not None
    assert placeholder_display.viewport.findChildren(DraggableHandle)

    placeholder_display.cleanup()
    QCoreApplication.processEvents()

    assert placeholder_display.findChildren(DraggablePanel) == []
    assert placeholder_display.viewport.findChildren(DraggableHandle) == []
    assert placeholder.parent() is None


def test_side_panel_hides_its_content_while_collapsed(qtbot: QtBot) -> None:
    panel = DraggablePanel(animation_duration=1)
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


def test_dragging_side_panel_fully_open_and_closed_updates_content_visibility(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(800, 400)
    layout = QHBoxLayout(host)
    handle = DraggableHandle(host)
    panel = DraggablePanel(host, animation_duration=1)
    layout.addWidget(handle)
    layout.addStretch()
    layout.addWidget(panel)
    handle.setTarget(panel)
    content = QLabel("Panel content")
    panel.set_content_widget(content)
    host.show()
    QCoreApplication.processEvents()

    try:
        for _ in range(2):
            start = handle.rect().center()
            opened = QPoint(start.x() - panel.expanded_width, start.y())
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, opened)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=opened)
            qtbot.waitUntil(content.isVisible)
            assert panel.width() == panel.expanded_width

            closed = QPoint(start.x() + panel.expanded_width, start.y())
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, closed)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=closed)
            qtbot.waitUntil(content.isHidden)
            assert panel.width() == panel.collapsed_width
    finally:
        handle.cleanup()
        panel.cleanup()


def test_side_panel_steps_aside_instead_of_squeezing_the_video(qtbot: QtBot) -> None:
    """An open panel takes at most half the pane, closes when the pane shrinks too far and reopens when it grows."""
    window = QWidget()
    qtbot.addWidget(window)
    layout = QHBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    display = FrameDisplay()
    layout.addWidget(display)
    display.enable_side_panel(QLabel("tools"))
    window.resize(1200, 400)
    window.show()
    panel = display.findChild(DraggablePanel)
    assert panel is not None

    def resize(width: int) -> None:
        window.resize(width, 400)
        QCoreApplication.processEvents()
        assert display.width() == width

    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)
    assert display.viewport.width() >= 1200 // 2

    resize(400)  # A half-pane panel would leave 200 px of video.
    assert not display.is_side_panel_open()
    assert display.viewport.width() == 400

    resize(1000)
    assert display.is_side_panel_open()
    assert display.viewport.width() >= 1000 // 2

    # A panel the user closed stays closed when room returns.
    display.set_side_panel_open(False)
    qtbot.waitUntil(lambda: panel.width() == 0)
    resize(400)
    resize(1200)
    assert not display.is_side_panel_open()

    # Opened by hand without room, it stays open while the pane narrows.
    resize(450)
    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)
    window.resize(420, 400)
    QCoreApplication.processEvents()
    assert display.is_side_panel_open()


def test_side_panel_opening_during_a_resize_ends_at_the_new_pane_width(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    layout = QHBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    display = FrameDisplay()
    layout.addWidget(display)
    display.enable_side_panel(QLabel("tools"))
    window.resize(1200, 400)
    window.show()
    panel = display.findChild(DraggablePanel)
    assert panel is not None

    display.set_side_panel_open(True)
    window.resize(560, 400)
    QCoreApplication.processEvents()

    qtbot.waitUntil(lambda: panel.width() == 280)
    assert display.viewport.width() == 280


def test_side_panel_refits_when_its_content_grows(qtbot: QtBot) -> None:
    """Content that grows, as after a larger text size, closes a panel that would leave the video too narrow."""
    window = QWidget()
    qtbot.addWidget(window)
    layout = QHBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    display = FrameDisplay()
    layout.addWidget(display)
    content = QLabel("tools")
    display.enable_side_panel(content)
    window.resize(800, 400)
    window.show()
    panel = display.findChild(DraggablePanel)
    assert panel is not None
    display.set_side_panel_open(True)
    qtbot.waitUntil(lambda: panel.width() == panel.expanded_width)

    content.setMinimumWidth(620)  # The video beside it would be under 240 px.
    qtbot.waitUntil(lambda: not display.is_side_panel_open())
    assert display.viewport.width() == 800

    # Once the content fits again, the next pane resize reopens the panel the user opened.
    content.setMinimumWidth(0)
    window.resize(820, 400)
    qtbot.waitUntil(display.is_side_panel_open)
