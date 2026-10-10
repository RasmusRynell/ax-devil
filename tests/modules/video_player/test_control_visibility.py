"""Tests for video-control visibility orchestration."""

from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.orchestration.control_visibility import ControlVisibilityController


def test_hovering_control_requests_visibility(qtbot: QtBot) -> None:
    parent = QObject()
    visibility_requests: list[str] = []
    controller = ControlVisibilityController(
        timer_parent=parent,
        idle_hide_delay_ms=2000,
        request_show=lambda: visibility_requests.append("show"),
        request_hide=lambda: visibility_requests.append("hide"),
    )

    controller.on_hover_enter("control_panel")

    assert visibility_requests == ["show"]
    controller.cleanup()


def test_idle_hides_controls_only_after_the_hovered_control_is_left(qtbot: QtBot) -> None:
    parent = QObject()
    visibility_requests: list[str] = []
    controller = ControlVisibilityController(
        timer_parent=parent,
        idle_hide_delay_ms=10,
        request_show=lambda: visibility_requests.append("show"),
        request_hide=lambda: visibility_requests.append("hide"),
    )

    try:
        # The idle timer is private, but its timeout is the only bounded wait for "idle elapsed".
        with qtbot.waitSignal(controller._idle_timer.timeout):
            controller.on_mouse_move()
            controller.on_hover_enter("control_panel")
        assert "hide" not in visibility_requests
        with qtbot.waitSignal(controller._idle_timer.timeout):
            controller.on_hover_leave("control_panel")
        assert visibility_requests[-1] == "hide"
    finally:
        controller.cleanup()
