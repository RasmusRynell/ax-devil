"""Overlay layout policy for video player UI overlays."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtWidgets import QWidget

_OVERLAY_MARGIN = 10
_OVERLAY_RIGHT_CENTER_MARGIN = 5


class OverlayPosition(str, Enum):
    """Supported placement modes for overlay widgets."""

    BOTTOM_CENTER = "bottom_center"
    BOTTOM_FULL_WIDTH = "bottom_full_width"
    TOP_LEFT = "top_left"
    FRAME_TOP_LEFT = "frame_top_left"
    FRAME_TOP_RIGHT = "frame_top_right"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    RIGHT_CENTER = "right_center"


@dataclass(frozen=True, slots=True)
class OverlayPlacement:
    """Resolved placement of an overlay widget within the display area."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class OverlayLayoutInstruction:
    """Layout result containing placement and optional widget sizing updates."""

    placement: OverlayPlacement
    fixed_width: int | None = None


def overlay_dimensions(overlay: QWidget) -> tuple[int, int]:
    """Return concrete overlay dimensions, falling back to size hints."""
    width = overlay.width() if overlay.width() > 0 else overlay.sizeHint().width()
    height = overlay.height() if overlay.height() > 0 else overlay.sizeHint().height()
    return width, height


def calculate_overlay_placement(
    position: OverlayPosition,
    *,
    overlay: QWidget,
    display_width: int,
    display_height: int,
    frame_rect: tuple[int, int, int, int] | None = None,
) -> OverlayLayoutInstruction:
    """Calculate overlay coordinates for the given placement strategy."""
    overlay_width, overlay_height = overlay_dimensions(overlay)

    if position == OverlayPosition.BOTTOM_CENTER:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=(display_width - overlay_width) // 2,
                y=display_height - overlay_height - _OVERLAY_MARGIN,
            )
        )

    if position == OverlayPosition.BOTTOM_FULL_WIDTH:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(x=0, y=display_height - overlay_height),
            fixed_width=display_width,
        )

    if position in {OverlayPosition.TOP_LEFT, OverlayPosition.FRAME_TOP_LEFT} and frame_rect is None:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(x=_OVERLAY_MARGIN, y=_OVERLAY_MARGIN),
        )

    if position == OverlayPosition.FRAME_TOP_LEFT and frame_rect is not None:
        frame_x, frame_y, _frame_width, _frame_height = frame_rect
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(x=frame_x + _OVERLAY_MARGIN, y=frame_y + _OVERLAY_MARGIN),
        )

    if position == OverlayPosition.FRAME_TOP_RIGHT and frame_rect is not None:
        frame_x, frame_y, frame_width, _frame_height = frame_rect
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=frame_x + frame_width - overlay_width - _OVERLAY_MARGIN,
                y=frame_y + _OVERLAY_MARGIN,
            ),
        )

    if position in {OverlayPosition.TOP_RIGHT, OverlayPosition.FRAME_TOP_RIGHT}:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=display_width - overlay_width - _OVERLAY_MARGIN,
                y=_OVERLAY_MARGIN,
            )
        )

    if position == OverlayPosition.BOTTOM_LEFT:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=_OVERLAY_MARGIN,
                y=display_height - overlay_height - _OVERLAY_MARGIN,
            )
        )

    if position == OverlayPosition.BOTTOM_RIGHT:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=display_width - overlay_width - _OVERLAY_MARGIN,
                y=display_height - overlay_height - _OVERLAY_MARGIN,
            )
        )

    if position == OverlayPosition.RIGHT_CENTER:
        return OverlayLayoutInstruction(
            placement=OverlayPlacement(
                x=display_width - overlay_width - _OVERLAY_RIGHT_CENTER_MARGIN,
                y=(display_height - overlay_height) // 2,
            )
        )

    return OverlayLayoutInstruction(
        placement=OverlayPlacement(
            x=(display_width - overlay_width) // 2,
            y=display_height - overlay_height - _OVERLAY_MARGIN,
        )
    )
