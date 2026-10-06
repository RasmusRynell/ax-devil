"""Tests for viewport overlay placement policy."""

from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.overlay_layout import OverlayPosition, calculate_overlay_placement


def test_frame_top_left_tracks_letterboxed_frame(qtbot: QtBot) -> None:
    overlay = QLabel("lane")
    qtbot.addWidget(overlay)

    instruction = calculate_overlay_placement(
        OverlayPosition.FRAME_TOP_LEFT,
        overlay=overlay,
        display_width=800,
        display_height=800,
        frame_rect=(0, 175, 800, 450),
    )

    assert instruction.placement.x == 10
    assert instruction.placement.y == 185


def test_frame_top_left_falls_back_to_viewport_before_first_frame(qtbot: QtBot) -> None:
    overlay = QLabel("lane")
    qtbot.addWidget(overlay)

    instruction = calculate_overlay_placement(
        OverlayPosition.FRAME_TOP_LEFT,
        overlay=overlay,
        display_width=800,
        display_height=800,
    )

    assert instruction.placement.x == 10
    assert instruction.placement.y == 10
