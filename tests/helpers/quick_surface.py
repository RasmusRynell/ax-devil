"""Builders for rendering overlays on a real Quick surface and reading the pixels back."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.drawing import (
    DrawingStyle,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import (
    DrawCall,
    DrawCalls,
    prepare_calls,
)

FILL = DrawingStyle(pen_width=0, brush_r=255, brush_g=0, brush_b=0, brush_a=255)


WHITE = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.04, font_size=0.18)


TARGET = QRectF(0, 0, 200, 100)


def solid_image(color: str = "black") -> QImage:
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def make_frame(image: QImage, primitives: DrawCalls, opacity: float = 1.0) -> VideoFrameWithOverlays:
    return VideoFrameWithOverlays(
        VideoFrame(image, 0),
        VideoOverlayData(
            lambda _context, buffer: prepare_calls(primitives, buffer), 0, metadata={"overlay_opacity": opacity}
        ),
    )


def open_surface(qtbot: QtBot) -> QuickSurface:
    widget = QuickSurface()
    qtbot.addWidget(widget, before_close_func=lambda widget: widget.cleanup())
    widget.resize(200, 100)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def render_calls(surface: QuickSurface, primitives: DrawCalls, opacity: float = 1.0) -> QImage:
    surface.set_frame_image(solid_image(), TARGET)
    surface.set_overlays(prepare_calls(primitives, surface.drawing_buffer(TARGET, 100)), opacity, TARGET, 100, None)
    QApplication.processEvents()
    return surface.grabFramebuffer()


@dataclass(frozen=True)
class CompoundCalls(DrawCall):
    parts: tuple[DrawCall, ...]

    def submit(self, canvas: DrawingBuffer) -> None:
        """Emit several ordered drawing operations through the general canvas contract."""
        for part in self.parts:
            part.submit(canvas)
