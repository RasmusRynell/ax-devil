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


def test_idle_does_not_hide_while_control_remains_hovered(qtbot: QtBot) -> None:
    parent = QObject()
    visibility_requests: list[str] = []
    controller = ControlVisibilityController(
        timer_parent=parent,
        idle_hide_delay_ms=10,
        request_show=lambda: visibility_requests.append("show"),
        request_hide=lambda: visibility_requests.append("hide"),
    )

    try:
        with qtbot.waitSignal(controller._idle_timer.timeout):
            controller.on_mouse_move()
            controller.on_hover_enter("control_panel")
        assert visibility_requests == ["show", "show"]
    finally:
        controller.cleanup()


def test_leaving_control_restarts_idle_hide_after_hovered_timeout(qtbot: QtBot) -> None:
    parent = QObject()
    visibility_requests: list[str] = []
    controller = ControlVisibilityController(
        timer_parent=parent,
        idle_hide_delay_ms=10,
        request_show=lambda: visibility_requests.append("show"),
        request_hide=lambda: visibility_requests.append("hide"),
    )

    try:
        with qtbot.waitSignal(controller._idle_timer.timeout):
            controller.on_mouse_move()
            controller.on_hover_enter("control_panel")
        assert visibility_requests == ["show", "show"]
        with qtbot.waitSignal(controller._idle_timer.timeout):
            controller.on_hover_leave("control_panel")
        assert visibility_requests == ["show", "show", "hide"]
    finally:
        controller.cleanup()
