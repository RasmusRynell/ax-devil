"""Tests for viewport overlay placement policy."""

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition, calculate_overlay_placement


def test_frame_top_left_tracks_letterboxed_frame(qtbot: QtBot) -> None:
    overlay = QLabel("lane")
    qtbot.addWidget(overlay)
    overlay.adjustSize()
    frame_x, frame_y, frame_width, frame_height = 0, 175, 800, 450

    instruction = calculate_overlay_placement(
        OverlayPosition.FRAME_TOP_LEFT,
        overlay=overlay,
        display_width=800,
        display_height=800,
        frame_rect=(frame_x, frame_y, frame_width, frame_height),
    )

    placement = instruction.placement
    assert frame_x <= placement.x < frame_x + frame_width // 2
    assert frame_y <= placement.y < frame_y + frame_height // 2


def test_frame_top_left_falls_back_to_viewport_before_first_frame(qtbot: QtBot) -> None:
    overlay = QLabel("lane")
    qtbot.addWidget(overlay)
    overlay.adjustSize()

    instruction = calculate_overlay_placement(
        OverlayPosition.FRAME_TOP_LEFT,
        overlay=overlay,
        display_width=800,
        display_height=800,
    )

    placement = instruction.placement
    assert 0 <= placement.x < 400
    assert 0 <= placement.y < 175  # Above where a letterboxed 16:9 frame would start.
