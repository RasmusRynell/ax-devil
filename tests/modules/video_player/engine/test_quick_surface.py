"""Video texture, culling and surface lifecycle contracts of the native Quick backend."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.chrome.theme import VIDEO_CANVAS
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.drawing import (
    DrawingStyle,
)
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer
from ax_devil.modules.video_player.engine.quick.prepared import EMPTY_DRAWING, PreparedDrawing
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from ax_devil.modules.video_player.engine.render_context import RenderContext
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawCall,
    DrawCalls,
    LineCall,
    PointCall,
    PolygonCall,
    PolylineCall,
    TextCall,
    prepare_calls,
)
from tests.helpers.quick_surface import (
    FILL,
    TARGET,
    WHITE,
    CompoundCalls,
    make_frame,
    render_calls,
    solid_image,
)


def test_texture_replacement_resize_reparent_and_clear(surface: QuickSurface, qtbot: QtBot) -> None:
    for color in ("red", "green", "blue", "white"):
        surface.set_frame_image(solid_image(color), TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(300, 200)
    surface.setParent(host)
    surface.resize(300, 200)
    surface.set_frame_image(solid_image("green"), QRectF(0, 0, 300, 200))
    host.show()
    surface.show()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(250, 150) == QColor("green")
    surface.clear()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == VIDEO_CANVAS
    surface.setParent(None)


def test_letterbox_stays_video_canvas_when_theme_changes(surface: QuickSurface) -> None:
    surface.set_frame_image(solid_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == VIDEO_CANVAS
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("magenta"))
    surface.setPalette(palette)
    surface.set_frame_image(solid_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == VIDEO_CANVAS
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("green")


def test_hidden_viewport_prepares_only_the_latest_frame_and_overlay_when_shown(qtbot: QtBot) -> None:
    stack = QStackedWidget()
    stack.resize(200, 100)
    widget = FrameViewport()
    qtbot.addWidget(stack, before_close_func=lambda _stack: widget.cleanup())
    stack.addWidget(widget)
    stack.addWidget(QWidget())
    stack.show()
    prepared: list[str] = []

    def frame(color: str) -> VideoFrameWithOverlays:
        def draw(_context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
            prepared.append(color)
            return prepare_calls([BoxCall(0, 0, 0.25 if color == "green" else 1, 1, FILL)], buffer)

        return VideoFrameWithOverlays(VideoFrame(solid_image(color), 0), VideoOverlayData(draw, 0))

    widget.display_frame(frame("blue"))
    stack.setCurrentIndex(1)  # Cancel preparation already queued before changing tabs.
    QApplication.processEvents()
    widget.display_frame(frame("white"))
    widget.display_frame(frame("green"))
    QApplication.processEvents()
    assert prepared == []

    stack.setCurrentIndex(0)
    QApplication.processEvents()
    surface = widget.findChild(QuickSurface)
    assert surface is not None
    image = surface.grabFramebuffer()
    assert prepared == ["green"]
    assert image.pixelColor(100, 50) == QColor("green")
    assert image.pixelColor(20, 50) == QColor("red")


def test_graphics_off_keeps_the_quick_surface(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GlobalSettings(), "graphics_acceleration", GraphicsAcceleration.OFF)
    widget = FrameViewport()
    qtbot.addWidget(widget)
    widget.resize(200, 100)
    widget.display_frame(make_frame(solid_image("blue"), [BoxCall(0.1, 0.1, 0.5, 0.5, FILL)]))
    widget.show()
    QApplication.processEvents()
    surface = widget.findChild(QuickSurface)
    assert surface is not None
    assert surface.grabFramebuffer().pixelColor(50, 30) == QColor("red")
    widget.cleanup()


def test_video_texture_handles_alpha_size_changes_and_latest_upload(surface: QuickSurface) -> None:
    surface.setClearColor(QColor("transparent"))  # Read the texture's own alpha, unblended with the canvas.
    for size, alpha in ((200, False), (200, True), (64, True), (200, False)):
        image = QImage(size, size, QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888)
        image.fill(QColor(180, 20, 40, 128 if alpha else 255))
        surface.set_frame_image(solid_image("blue"), TARGET)
        surface.set_frame_image(image, TARGET)
        QApplication.processEvents()
        actual = surface.grabFramebuffer().pixelColor(100, 50)
        assert abs(actual.red() - 180) <= 2
        assert abs(actual.green() - 20) <= 2
        assert abs(actual.blue() - 40) <= 2
        assert abs(actual.alpha() - (128 if alpha else 255)) <= 2


def test_hardware_video_storage_is_retained_for_same_size_frames(surface: QuickSurface) -> None:
    surface.set_frame_image(solid_image("red"), TARGET)
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("red")
    texture = surface._video._texture
    for color in ("blue", "green", "white"):
        surface.set_frame_image(solid_image(color), TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
        # Software has no RHI texture; the same test also runs on native OpenGL.
        assert surface._video._texture is texture
    surface.cleanup()
    if texture is not None:
        assert not isValid(texture)


def present_target(surface: QuickSurface, primitives: DrawCalls, target: QRectF, *, cull: bool = True) -> QImage:
    surface.set_frame_image(solid_image(), target)
    buffer = surface.drawing_buffer(target, min(target.width(), target.height()))
    if not cull:
        buffer.reset(replace(buffer.settings, viewport=None))
    surface.set_overlays(
        prepare_calls(primitives, buffer),
        0.7,
        target,
        min(target.width(), target.height()),
        None,
    )
    QApplication.processEvents()
    return surface.grabFramebuffer()


@pytest.mark.parametrize("target", [TARGET, QRectF(-180, -20, 400, 200), QRectF(20, 20, 160, 60)])
def test_culling_matches_unculled_output(surface: QuickSurface, target: QRectF) -> None:
    """Whole operations disappear only when no pixels can reach the visible surface."""
    style = replace(WHITE, pen_width=0.08, brush_r=80, brush_a=120)
    primitives: DrawCalls = [
        BoxCall(-0.1, 0.1, 0.2, 0.6, style),
        BoxCall(1.01, 0.1, 0.3, 0.6, style),
        CircleCall(0.5, -0.02, 0.2, style),
        LineCall(-0.01, 0.7, -0.04, 1.1, style),
        LineCall(0.9, 0.9, 1.1, 1.1, replace(style, pen_style="dash")),
        PointCall(1.01, 0.5, style),
        PolygonCall(((-0.2, 0.4), (0.4, 1.1), (0.7, 0.6)), style),
        PolylineCall(((0.8, 0.2), (1.1, 0.5), (1.2, 0.1)), style),
        TextCall(1.1, 0.4, "Visible overhang\nSecond line", style, anchor="center-right"),
        TextCall(-0.1, 0.8, "Åj العربية", style, anchor="baseline"),
        TextCall(0.4, 1.1, "A\u0301\u0301\u0301\u0301\u0301\u0301\u0301\u0301", style, anchor="top-left"),
    ]
    for x in (-10.0, 10.0):
        primitives.extend(
            [
                BoxCall(x, 0.2, 0.1, 0.1, style),
                CircleCall(x, 0.5, 0.2, style),
                LineCall(x, 0.1, x + 0.1, 0.2, style),
                PointCall(x, 0.5, style),
                PolygonCall(((x, 0.1), (x + 0.1, 0.1), (x, 0.2)), style),
                PolylineCall(((x, 0.1), (x, 0.2)), style),
                TextCall(x, 0.5, "Offscreen", style),
            ]
        )
    # Compare each operation independently: removing offscreen siblings can change
    # Qt's batching and accumulated blending roundoff in the composite image.
    for primitive in primitives:
        actual = present_target(surface, [primitive], target)
        present_target(surface, [], target)
        expected = present_target(surface, [primitive], target, cull=False)
        difference = np.abs(
            np.frombuffer(actual.constBits(), dtype=np.uint8).astype(np.int16)
            - np.frombuffer(expected.constBits(), dtype=np.uint8).astype(np.int16)
        )
        assert difference.max() <= 1, f"{primitive}"


def test_pan_and_viewport_resize_reveal_unchanged_instructions(surface: QuickSurface) -> None:
    """Viewport context must invalidate both frame and individual-operation reuse."""
    primitives: DrawCalls = [
        BoxCall(0.75, 0.1, 0.2, 0.5, FILL),
        TextCall(0.75, 0.1, "Text", WHITE, anchor="top-left"),
    ]
    left = QRectF(0, 0, 400, 100)
    right = QRectF(-200, 0, 400, 100)
    present_target(surface, primitives, left)
    assert surface._overlays._drawing == EMPTY_DRAWING
    visible = present_target(surface, primitives, right)
    assert visible.pixelColor(120, 40).red() > 100
    present_target(surface, primitives, left)
    assert surface._overlays._drawing == EMPTY_DRAWING
    surface.resize(400, 100)
    resized = present_target(surface, primitives, left)
    assert resized.pixelColor(320, 40).red() > 100


@dataclass(frozen=True)
class UnboundedPath(DrawCall):
    def submit(self, canvas: DrawingBuffer) -> None:
        """Custom geometry without declared bounds stays supported."""
        canvas.path("m 0 0 h 30 v 30 h -30 z", FILL, fill=True, origin=QPointF(20, 20))


def test_culling_keeps_paths_without_bounds_and_drops_offscreen_rows(surface: QuickSurface) -> None:
    """Off-screen rows are culled while a path without declared bounds stays.

    The box and path do not overlap: they may land in different layers, whose order is fixed by kind, not emission.
    """
    compound = CompoundCalls(
        (
            BoxCall(10, 10, 1, 1, FILL),
            UnboundedPath(),
            BoxCall(0.3, 0.3, 0.2, 0.4, replace(FILL, brush_r=0, brush_b=255)),
            TextCall(10, 10, "Hidden", WHITE),
        )
    )
    actual = render_calls(surface, [compound])
    assert actual.pixelColor(25, 25) == QColor("red")
    assert actual.pixelColor(80, 50) == QColor("blue")
    assert render_calls(surface, [BoxCall(-10, -10, 1, 1, FILL), compound]) == actual


def test_empty_geometry_can_become_visible(surface: QuickSurface) -> None:
    """An initially empty batch must not leave a stale zero-vertex scene-graph node."""
    empty = DrawingStyle(pen_width=0, brush_a=0)
    first = render_calls(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, empty)])
    assert first.pixelColor(50, 30) == QColor("black")
    second = render_calls(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, FILL)])
    assert second.pixelColor(50, 30) == QColor("red")
