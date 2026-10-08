"""Rendered-output and lifecycle contracts for the native Quick backend."""

from __future__ import annotations

import gc
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.drawing import (
    DrawingStyle,
    LabelContent,
    LabelRun,
    LabelSprite,
    Paint,
    Points,
    _rectangle_path,
)
from ax_devil.modules.video_player.engine.quick import labels as label_layer
from ax_devil.modules.video_player.engine.quick.labels import _LabelNode
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, LabelData
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawCall,
    DrawCalls,
    LabelCall,
    LineCall,
    Numbers,
    PointCall,
    PolygonCall,
    PolylineCall,
    TextCall,
    prepare_calls,
)

_FILL = DrawingStyle(pen_width=0, brush_r=255, brush_g=0, brush_b=0, brush_a=255)
_WHITE = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.04, font_size=0.18)
_TARGET = QRectF(0, 0, 200, 100)


def _image(color: str = "black") -> QImage:
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def _frame(image: QImage, primitives: DrawCalls, opacity: float = 1.0) -> VideoFrameWithOverlays:
    return VideoFrameWithOverlays(
        VideoFrame(image, 0),
        VideoOverlayData(
            lambda _context, buffer: prepare_calls(primitives, buffer), 0, metadata={"overlay_opacity": opacity}
        ),
    )


@pytest.fixture
def surface(qtbot: QtBot) -> QuickSurface:
    """Create a real scene graph surface (software in ordinary offscreen CI)."""
    return _surface(qtbot)


@pytest.fixture
def other_surface(qtbot: QtBot) -> QuickSurface:
    """Retain the second viewer through QtBot's native widget teardown."""
    return _surface(qtbot)


def _surface(qtbot: QtBot) -> QuickSurface:
    widget = QuickSurface()
    qtbot.addWidget(widget, before_close_func=lambda widget: widget.cleanup())
    widget.resize(200, 100)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _render(surface: QuickSurface, primitives: DrawCalls, opacity: float = 1.0) -> QImage:
    surface.set_frame_image(_image(), _TARGET)
    surface.set_overlays(prepare_calls(primitives, surface.drawing_buffer(_TARGET, 100)), opacity, _TARGET, 100, None)
    QApplication.processEvents()
    return surface.grabFramebuffer()


@pytest.mark.parametrize(
    "primitive,inside,outside",
    [
        (BoxCall(0.1, 0.1, 0.5, 0.5, _FILL), (50, 30), (150, 30)),
        (CircleCall(0.5, 0.5, 0.2, _FILL), (100, 50), (140, 50)),
        (
            PolygonCall(((0.1, 0.1), (0.9, 0.1), (0.9, 0.3), (0.3, 0.3), (0.3, 0.9), (0.1, 0.9)), _FILL),
            (30, 70),
            (140, 70),
        ),
        (PolygonCall(((0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)), _FILL), (100, 30), (50, 50)),
        (LineCall(0.1, 0.5, 0.9, 0.5, _WHITE), (100, 50), (100, 60)),
        (PointCall(0.5, 0.5, _WHITE), (100, 50), (110, 50)),
        (PolylineCall(((0.1, 0.2), (0.5, 0.2), (0.5, 0.8)), _WHITE), (100, 60), (60, 60)),
    ],
)
def test_general_geometry_has_expected_coverage(
    surface: QuickSurface,
    primitive: DrawCall,
    inside: tuple[int, int],
    outside: tuple[int, int],
) -> None:
    actual = _render(surface, [primitive])
    assert actual.pixelColor(*inside) != QColor("black")
    assert actual.pixelColor(*outside) == QColor("black")


def test_order_opacity_and_removal_survive_reused_path_slots(surface: QuickSurface) -> None:
    blue = DrawingStyle(pen_width=0, brush_b=255, brush_a=255)
    primitives: DrawCalls = [BoxCall(0.1, 0.1, 0.6, 0.6, _FILL), BoxCall(0.3, 0.3, 0.6, 0.6, blue)]
    actual = _render(surface, primitives, 0.5)
    for point, expected in (
        ((40, 20), QColor(128, 0, 0)),
        ((100, 50), QColor(64, 0, 128)),
        ((170, 80), QColor(0, 0, 128)),
    ):
        color = actual.pixelColor(*point)
        assert abs(color.red() - expected.red()) <= 2
        assert abs(color.blue() - expected.blue()) <= 2
    actual = _render(surface, [primitives[0]])
    assert actual.pixelColor(100, 50) == QColor("red")
    assert actual.pixelColor(170, 80) == QColor("black")
    assert _render(surface, []).pixelColor(40, 20) == QColor("black")


def test_multiline_text_draws_above_geometry_in_any_emission_order(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Hello\nWorld", _WHITE, anchor="top-left")
    rendered = _render(surface, [text])
    lit_top = sum(rendered.pixelColor(x, y).red() > 100 for y in range(10, 30) for x in range(20, 100))
    lit_bottom = sum(rendered.pixelColor(x, y).red() > 100 for y in range(30, 60) for x in range(20, 100))
    assert lit_top > 30
    assert lit_bottom > 30
    box = BoxCall(0, 0, 1, 1, _FILL)
    orders: list[DrawCalls] = [[text, box], [box, text]]
    for calls in orders:
        visible = _render(surface, calls)
        assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 60) for x in range(20, 100))
        assert visible.pixelColor(190, 90) == QColor("red")


def test_dash_zero_stroke_and_zero_area(surface: QuickSurface) -> None:
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=0.03, pen_style="dash")
    actual = _render(surface, [LineCall(0.1, 0.5, 0.9, 0.5, style)])
    colors = [actual.pixelColor(x, 50).red() for x in range(25, 175)]
    assert max(colors) == 255 and min(colors) == 0
    invisible: DrawCalls = [
        BoxCall(0.1, 0.1, 0.5, 0.5, DrawingStyle(pen_width=0)),
        BoxCall(0.2, 0.2, 0, 0.5),
        CircleCall(0.5, 0.5, 0),
        PointCall(0.5, 0.5, DrawingStyle(pen_width=0)),
    ]
    assert _render(surface, invisible).convertToFormat(QImage.Format.Format_RGB32) == _image()


def test_texture_replacement_resize_reparent_and_clear(surface: QuickSurface, qtbot: QtBot) -> None:
    for color in ("red", "green", "blue", "white"):
        surface.set_frame_image(_image(color), _TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(300, 200)
    surface.setParent(host)
    surface.resize(300, 200)
    surface.set_frame_image(_image("green"), QRectF(0, 0, 300, 200))
    host.show()
    surface.show()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(250, 150) == QColor("green")
    surface.clear()
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == surface.palette().color(QPalette.ColorRole.Window)
    surface.setParent(None)


def test_letterbox_follows_host_palette(surface: QuickSurface) -> None:
    surface.set_frame_image(_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == surface.palette().color(QPalette.ColorRole.Window)
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("magenta"))
    surface.setPalette(palette)
    surface.set_frame_image(_image("green"), QRectF(50, 0, 100, 100))
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(10, 50) == QColor("magenta")
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("green")


def test_latest_frame_and_overlay_are_submitted_together(qtbot: QtBot) -> None:
    widget = FrameViewport()
    qtbot.addWidget(widget)
    widget.resize(200, 100)
    widget.show()
    widget.display_frame(_frame(_image("blue"), [BoxCall(0, 0, 1, 1, _FILL)]))
    widget.display_frame(_frame(_image("green"), []))
    QApplication.processEvents()
    surface = widget.findChild(QuickSurface)
    assert surface is not None
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("green")
    widget.cleanup()


def test_replacing_values_in_producer_list_updates_geometry(surface: QuickSurface) -> None:
    box = BoxCall(0.1, 0.1, 0.2, 0.2, _FILL)
    primitives: DrawCalls = [box]
    assert _render(surface, primitives).pixelColor(30, 20) == QColor("red")
    primitives[0] = replace(box, x=0.6)
    moved = _render(surface, primitives)
    assert moved.pixelColor(30, 20) == QColor("black")
    assert moved.pixelColor(130, 20) == QColor("red")
    polygon = PolygonCall(((0.1, 0.1), (0.3, 0.1), (0.3, 0.3)), _FILL)
    primitives[:] = [polygon]
    assert _render(surface, primitives).pixelColor(50, 15) == QColor("red")
    primitives[0] = replace(polygon, points=((0.6, 0.1), (0.8, 0.1), (0.8, 0.3)))
    moved = _render(surface, primitives)
    assert moved.pixelColor(50, 15) == QColor("black")
    assert moved.pixelColor(150, 15) == QColor("red")


def test_moving_text_updates_position_and_replaces_glyphs(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Wide", _WHITE, anchor="top-left")
    first = _render(surface, [text])
    text = replace(text, x=0.6)
    moved = _render(surface, [text])
    assert any(first.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(20, 70))
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(20, 70))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(120, 175))
    text = replace(text, text="Other")
    assert _render(surface, [text]) != moved


def test_graphics_off_keeps_the_quick_surface(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(GlobalSettings(), "_graphics_acceleration", GraphicsAcceleration.OFF)
    widget = FrameViewport()
    qtbot.addWidget(widget)
    widget.resize(200, 100)
    widget.display_frame(_frame(_image("blue"), [BoxCall(0.1, 0.1, 0.5, 0.5, _FILL)]))
    widget.show()
    QApplication.processEvents()
    surface = widget.findChild(QuickSurface)
    assert surface is not None
    assert surface.grabFramebuffer().pixelColor(50, 30) == QColor("red")
    widget.cleanup()


def test_independent_path_movement_and_style_changes(surface: QuickSurface) -> None:
    """Moving a neighboring shape must not alter retained geometry or paint order."""
    red = BoxCall(0.1, 0.1, 0.3, 0.5, _FILL)
    blue_style = replace(_FILL, brush_r=0, brush_b=255)
    blue = BoxCall(0.5, 0.1, 0.3, 0.5, blue_style)
    _render(surface, [red, blue])
    moved = _render(surface, [red, replace(blue, x=0.2)])
    assert moved.pixelColor(30, 30) == QColor("red")
    assert moved.pixelColor(50, 30) == QColor("blue")
    assert moved.pixelColor(140, 30) == QColor("black")
    changed = _render(surface, [red, replace(blue, w=0.1, style=_FILL)])
    assert changed.pixelColor(110, 30) == QColor("red")
    assert changed.pixelColor(140, 30) == QColor("black")


def test_independent_text_movement_color_and_slot_replacement(surface: QuickSurface) -> None:
    """Consecutive labels move separately and replaced slots leave no stale glyphs."""
    first = TextCall(0.05, 0.1, "Left", _WHITE, anchor="top-left")
    second = TextCall(0.55, 0.1, "Right", _WHITE, anchor="top-left")
    original = _render(surface, [first, second])
    moved = _render(surface, [first, replace(second, y=0.6, style=replace(_WHITE, pen_g=0, pen_b=0))])
    assert original.copy(0, 0, 100, 100) == moved.copy(0, 0, 100, 100)
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(110, 190))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(60, 85) for x in range(110, 190))
    covered = _render(surface, [BoxCall(0, 0, 1, 1, _FILL)])
    assert covered.convertToFormat(QImage.Format.Format_RGB32) == _image("red")
    assert _render(surface, [first, second]) == original


def test_removing_a_label_keeps_remaining_glyphs_on_their_items(surface: QuickSurface) -> None:
    """Earlier removals must not shift later labels onto items that rebuild their glyphs."""
    labels: DrawCalls = [TextCall(0.05 + 0.3 * i, 0.1, name, _WHITE, anchor="top-left") for i, name in enumerate("ABC")]
    _render(surface, labels)
    before = {id(item._block): item for item in surface._overlays._texts.items if item.isVisible()}
    remaining = _render(surface, labels[1:])
    after = {id(item._block): item for item in surface._overlays._texts.items if item.isVisible()}
    assert len(after) == 2
    assert all(after[block] is before[block] for block in after)
    assert all(remaining.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(5, 50))
    assert any(remaining.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(65, 110))


def test_zero_output_instruction_changes_slot_alignment(surface: QuickSurface) -> None:
    """An invisible instruction becoming visible must shift subsequent retained slots."""
    invisible = BoxCall(0, 0, 0, 0.5, _FILL)
    blue = BoxCall(0.5, 0, 0.5, 1, replace(_FILL, brush_r=0, brush_b=255))
    _render(surface, [invisible, blue])
    visible = _render(surface, [replace(invisible, w=0.5), blue])
    assert visible.pixelColor(50, 25) == QColor("red")
    assert visible.pixelColor(150, 25) == QColor("blue")
    hidden = _render(surface, [invisible, blue])
    assert hidden.pixelColor(50, 25) == QColor("black")
    assert hidden.pixelColor(150, 25) == QColor("blue")


@dataclass(frozen=True)
class _CompoundCalls(DrawCall):
    parts: tuple[DrawCall, ...]

    def submit(self, canvas: DrawingBuffer) -> None:
        """Emit several ordered drawing operations through the general canvas contract."""
        for part in self.parts:
            part.submit(canvas)


def test_unchanged_compound_calls_survive_neighbor_updates(surface: QuickSurface) -> None:
    """Partial updates preserve groups of interleaved drawing calls."""
    box = BoxCall(0, 0, 0.4, 1, _FILL)
    compound = _CompoundCalls(
        (
            BoxCall(0.5, 0, 0.5, 1, replace(_FILL, brush_r=0, brush_b=255)),
            TextCall(0.55, 0.1, "Text", _WHITE, anchor="top-left"),
        )
    )
    original = _render(surface, [box, compound])
    for replacement in (replace(box, w=0.2), replace(box, w=0), box):
        updated = _render(surface, [replacement, compound])
        assert updated.copy(100, 0, 100, 100) == original.copy(100, 0, 100, 100)


def test_operation_kind_reordering_reuses_bounded_pools(surface: QuickSurface) -> None:
    box = BoxCall(0, 0, 1, 1, _FILL)
    text = TextCall(0.1, 0.1, "Visible", _WHITE, anchor="top-left")
    _render(surface, [text, box])
    drawing_pool = surface._overlays._geometry or surface._overlays._paths.items
    drawing_item = drawing_pool[0]
    text_item = surface._overlays._texts.items[0]
    visible = _render(surface, [box, text])
    assert drawing_pool[0] is drawing_item
    assert surface._overlays._texts.items[0] is text_item
    assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 40) for x in range(20, 100))
    covered = _render(surface, [text, box])
    assert any(covered.pixelColor(x, y).green() > 100 for y in range(10, 40) for x in range(20, 100))

    _render(surface, [box] * 80)
    _render(surface, [box])
    assert len(surface._overlays._paths.items) <= 33
    assert len(surface._overlays._geometry) <= 33
    assert len(surface._overlays._texts.items) <= 32
    _render(surface, [])
    assert not surface._overlays._paths.items and not surface._overlays._texts.items and not surface._overlays._geometry


def test_video_texture_handles_alpha_size_changes_and_latest_upload(surface: QuickSurface) -> None:
    palette = surface.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("transparent"))
    surface.setPalette(palette)
    for size, alpha in ((200, False), (200, True), (64, True), (200, False)):
        image = QImage(size, size, QImage.Format.Format_RGBA8888 if alpha else QImage.Format.Format_RGB888)
        image.fill(QColor(180, 20, 40, 128 if alpha else 255))
        surface.set_frame_image(_image("blue"), _TARGET)
        surface.set_frame_image(image, _TARGET)
        QApplication.processEvents()
        actual = surface.grabFramebuffer().pixelColor(100, 50)
        assert abs(actual.red() - 180) <= 2
        assert abs(actual.green() - 20) <= 2
        assert abs(actual.blue() - 40) <= 2
        assert abs(actual.alpha() - (128 if alpha else 255)) <= 2


def test_hardware_video_storage_is_retained_for_same_size_frames(surface: QuickSurface) -> None:
    surface.set_frame_image(_image("red"), _TARGET)
    QApplication.processEvents()
    assert surface.grabFramebuffer().pixelColor(100, 50) == QColor("red")
    texture = surface._video._texture
    for color in ("blue", "green", "white"):
        surface.set_frame_image(_image(color), _TARGET)
        QApplication.processEvents()
        assert surface.grabFramebuffer().pixelColor(100, 50) == QColor(color)
        # Software has no RHI texture; the same test also runs on native OpenGL.
        assert surface._video._texture is texture
    surface.cleanup()
    if texture is not None:
        assert not isValid(texture)


def _reference_boxes(canvas: DrawingBuffer, x: Numbers, y: Numbers, w: Numbers, h: Numbers, paint: Paint) -> None:
    s = canvas.settings
    for row in range(len(x)):
        canvas.path(
            _rectangle_path(float(w[row]) * s.width, float(h[row]) * s.height),
            paint.style(row),
            fill=True,
            origin=QPointF(x[row] * s.width, y[row] * s.height),
        )


def _reference_lines(canvas: DrawingBuffer, x1: Numbers, y1: Numbers, x2: Numbers, y2: Numbers, paint: Paint) -> None:
    s = canvas.settings
    for row in range(len(x1)):
        dx, dy = (x2[row] - x1[row]) * s.width, (y2[row] - y1[row]) * s.height
        canvas.path(
            f"m 0 0 l {dx:.12g} {dy:.12g}",
            paint.style(row),
            fill=False,
            origin=QPointF(x1[row] * s.width, y1[row] * s.height),
        )


def _reference_polygons(canvas: DrawingBuffer, points: Sequence[Points], paint: Paint) -> None:
    for row, shape in enumerate(points):
        canvas._polygon(shape, paint.style(row), True)


@pytest.mark.parametrize("stroke", [0.005, 0.01, 0.02, 0.06])
@pytest.mark.parametrize("alpha", [15, 220, 255])
def test_numeric_geometry_preserves_path_coverage(
    surface: QuickSurface, monkeypatch: pytest.MonkeyPatch, stroke: float, alpha: int
) -> None:
    """Compare fractional, translucent, beveled and reversed geometry with Qt paths, one primitive at a time.

    Edge coverage is approximate, but interiors, bounds and opacity must agree. Order within the
    geometry layer is not preserved, so overlapping primitives are not compared together.
    Run this test with native OpenGL and QT_SCALE_FACTOR=1, 1.5 and 2 as well as software.
    """
    style = DrawingStyle(pen_r=255, pen_g=255, pen_b=255, pen_width=stroke, brush_g=180, brush_a=alpha)
    primitives: DrawCalls = [
        BoxCall(0.10125, 0.1025, 0.35, 0.35, style),
        BoxCall(0.35, 0.2, 0.2, 0.6, replace(style, pen_width=0)),
        LineCall(0.9, 0.9, 0.6, 0.2, style),
        LineCall(0.05, 0.6, 0.4, 0.6, style),
        LineCall(0.55, 0.1, 0.55, 0.4, style),
        PolygonCall(((0.62, 0.62), (0.95, 0.66), (0.9, 0.95), (0.66, 0.9)), style),
        PolygonCall(((0.1, 0.95), (0.2, 0.7), (0.3, 0.95)), replace(style, pen_width=0)),
    ]
    _render(surface, primitives, 0.6)
    hardware = surface.quickWindow().rendererInterface().graphicsApi() != QSGRendererInterface.GraphicsApi.Software
    assert bool(surface._overlays._geometry) == hardware
    for primitive in primitives:
        _render(surface, [])
        actual = _render(surface, [primitive], 0.6).convertToFormat(QImage.Format.Format_RGBA8888)
        _render(surface, [])
        with monkeypatch.context() as patch:
            patch.setattr(DrawingBuffer, "boxes", _reference_boxes)
            patch.setattr(DrawingBuffer, "lines", _reference_lines)
            patch.setattr(DrawingBuffer, "polygons", _reference_polygons)
            expected = _render(surface, [primitive], 0.6).convertToFormat(QImage.Format.Format_RGBA8888)
        shape = (actual.height(), actual.width(), 4)
        actual_pixels = np.frombuffer(actual.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        expected_pixels = np.frombuffer(expected.constBits(), dtype=np.uint8).reshape(shape).astype(np.int16)
        difference = np.abs(actual_pixels - expected_pixels)
        # At fractional DPR Qt's square-cap endpoint can be a half-coverage sample
        # brighter than the numeric fringe. Keep differences confined to boundaries.
        assert difference.max() <= 80, f"{primitive}"
        assert difference.mean() < 0.8
        uniform = np.ones(shape[:2], dtype=np.bool_)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                neighbor = np.roll(expected_pixels, (dy, dx), axis=(0, 1))
                uniform &= np.max(np.abs(expected_pixels - neighbor), axis=2) <= 2
        assert difference[uniform].max() <= 2


def test_geometry_and_path_transitions_preserve_order_and_pool_alignment(surface: QuickSurface) -> None:
    """Changing eligibility must not reuse a neighboring operation's retained contents."""
    first = BoxCall(0.1, 0.1, 0.6, 0.6, _FILL)
    compound = _CompoundCalls(
        (
            CircleCall(0.6, 0.5, 0.2, replace(_FILL, brush_r=0, brush_b=255)),
            BoxCall(0.6, 0.1, 0.3, 0.4, replace(_FILL, brush_r=0, brush_g=255)),
            TextCall(0.6, 0.1, "Text", _WHITE, anchor="top-left"),
        )
    )
    original = _render(surface, [first, compound])
    for replacement in (
        replace(first, style=replace(_FILL, pen_style="dash")),
        replace(first, w=0.002),
        LineCall(0.1, 0.1, 0.3, 0.3, _WHITE),
        first,
    ):
        updated = _render(surface, [replacement, compound])
        assert updated.copy(150, 0, 50, 100) == original.copy(150, 0, 50, 100)
    assert _render(surface, [first, compound]) == original


def _present_target(surface: QuickSurface, primitives: DrawCalls, target: QRectF, *, cull: bool = True) -> QImage:
    surface.set_frame_image(_image(), target)
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


@pytest.mark.parametrize("target", [_TARGET, QRectF(-180, -20, 400, 200), QRectF(20, 20, 160, 60)])
def test_culling_matches_unculled_output(surface: QuickSurface, target: QRectF) -> None:
    """Whole operations disappear only when no pixels can reach the visible surface."""
    style = replace(_WHITE, pen_width=0.08, brush_r=80, brush_a=120)
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
    _present_target(surface, primitives, target)
    count = len(surface._overlays._drawing)
    _present_target(surface, [], target)
    _present_target(surface, primitives, target, cull=False)
    assert len(surface._overlays._drawing) > count
    # Compare each operation independently: removing offscreen siblings can change
    # Qt's batching and accumulated blending roundoff in the composite image.
    for primitive in primitives:
        actual = _present_target(surface, [primitive], target)
        _present_target(surface, [], target)
        expected = _present_target(surface, [primitive], target, cull=False)
        difference = np.abs(
            np.frombuffer(actual.constBits(), dtype=np.uint8).astype(np.int16)
            - np.frombuffer(expected.constBits(), dtype=np.uint8).astype(np.int16)
        )
        assert difference.max() <= 1, f"{primitive}"


def test_pan_and_viewport_resize_reveal_unchanged_instructions(surface: QuickSurface) -> None:
    """Viewport context must invalidate both frame and individual-operation reuse."""
    primitives: DrawCalls = [
        BoxCall(0.75, 0.1, 0.2, 0.5, _FILL),
        TextCall(0.75, 0.1, "Text", _WHITE, anchor="top-left"),
    ]
    left = QRectF(0, 0, 400, 100)
    right = QRectF(-200, 0, 400, 100)
    _present_target(surface, primitives, left)
    assert len(surface._overlays._drawing) == 0
    visible = _present_target(surface, primitives, right)
    assert len(surface._overlays._drawing) == 2
    assert visible.pixelColor(120, 40).red() > 100
    _present_target(surface, primitives, left)
    assert len(surface._overlays._drawing) == 0
    surface.resize(400, 100)
    _present_target(surface, primitives, left)
    assert len(surface._overlays._drawing) == 2


@dataclass(frozen=True)
class _UnboundedPath(DrawCall):
    def submit(self, canvas: DrawingBuffer) -> None:
        """Custom geometry without declared bounds stays supported."""
        canvas.path("m 0 0 h 30 v 30 h -30 z", _FILL, fill=True, origin=QPointF(20, 20))


def test_culling_keeps_paths_without_bounds_and_drops_offscreen_rows(surface: QuickSurface) -> None:
    """Off-screen rows are culled while a path without declared bounds stays.

    The box and path do not overlap: they may land in different layers, whose order is fixed by kind, not emission.
    """
    compound = _CompoundCalls(
        (
            BoxCall(10, 10, 1, 1, _FILL),
            _UnboundedPath(),
            BoxCall(0.3, 0.3, 0.2, 0.4, replace(_FILL, brush_r=0, brush_b=255)),
            TextCall(10, 10, "Hidden", _WHITE),
        )
    )
    actual = _render(surface, [compound])
    assert len(surface._overlays._drawing) == 2
    assert actual.pixelColor(25, 25) == QColor("red")
    assert actual.pixelColor(80, 50) == QColor("blue")
    assert _render(surface, [BoxCall(-10, -10, 1, 1, _FILL), compound]) == actual


def test_empty_geometry_can_become_visible(surface: QuickSurface) -> None:
    """An initially empty batch must not leave a stale zero-vertex scene-graph node."""
    empty = DrawingStyle(pen_width=0, brush_a=0)
    first = _render(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, empty)])
    assert first.pixelColor(50, 30) == QColor("black")
    second = _render(surface, [BoxCall(0.1, 0.1, 0.5, 0.5, _FILL)])
    assert second.pixelColor(50, 30) == QColor("red")


def test_label_draws_its_background_above_geometry_and_moves_without_repainting(surface: QuickSurface) -> None:
    content = LabelContent((LabelRun("Person", (255, 255, 255), "semibold"),), 0.12, background=(0, 0, 255, 255))
    label = LabelCall(0.1, 0.1, "top-left", replace(content, padding_x=0.06, padding_y=0.04))
    rendered = _render(surface, [BoxCall(0, 0, 1, 1, _FILL), label])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(190, 90) == QColor("red")
    sprite = surface._overlays._drawing.labels[0].sprite
    moved = _render(surface, [replace(label, x=0.5)])
    assert moved.pixelColor(22, 12) == QColor("black")
    assert moved.pixelColor(102, 12) == QColor("blue")
    assert surface._overlays._drawing.labels[0].sprite is sprite


def test_label_texture_replacement_preserves_neighboring_images(surface: QuickSurface) -> None:
    """Distinct label images keep their own pixels when one changes or releases its atlas allocation.

    The software backend has no atlas; run this on OpenGL as ``docs/runbooks/testing.md`` describes to cover it.
    """
    content = LabelContent(
        (LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_x=0.06, padding_y=0.04
    )
    first = LabelCall(0.1, 0.1, "top-left", content)
    second = LabelCall(0.6, 0.1, "top-left", replace(content, background=(0, 255, 0, 255)))
    rendered = _render(surface, [first, second])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(122, 12) == QColor(0, 255, 0)

    changed = replace(first, content=replace(content, background=(255, 255, 0, 255)))
    rendered = _render(surface, [changed, second])
    assert rendered.pixelColor(22, 12) == QColor("yellow")
    assert rendered.pixelColor(122, 12) == QColor(0, 255, 0)
    rendered = _render(surface, [second])
    assert rendered.pixelColor(22, 12) == QColor("black")
    assert rendered.pixelColor(122, 12) == QColor(0, 255, 0)


@pytest.fixture
def label_nodes(monkeypatch: pytest.MonkeyPatch) -> list[_LabelNode]:
    """Observe render-owned trees to verify reuse and actual native resource destruction."""
    nodes: list[_LabelNode] = []
    present = _LabelNode.present

    def record(node: _LabelNode, window: QQuickWindow, labels: tuple[LabelData, ...]) -> None:
        present(node, window, labels)
        if not nodes or node is not nodes[-1]:
            nodes.append(node)

    monkeypatch.setattr(_LabelNode, "present", record)
    return nodes


def test_labels_share_texture_reuse_returning_content_and_release_on_clear(
    surface: QuickSurface, label_nodes: list[_LabelNode]
) -> None:
    """Repeated and returning labels need one upload; clearing releases even cached textures."""
    blue = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    first = LabelCall(0.1, 0.1, "top-left", blue)
    second = replace(first, x=0.6)
    rendered = _render(surface, [first, second])
    assert rendered.pixelColor(21, 12) == rendered.pixelColor(121, 12) == QColor("blue")
    node = label_nodes[-1]
    ((sprite, group),) = node.groups.items()
    texture = group.texture
    assert len(group.images) == 2
    changed = replace(first, content=replace(blue, background=(255, 255, 0, 255)))
    rendered = _render(surface, [changed])
    assert rendered.pixelColor(21, 12) == QColor("yellow")
    assert rendered.pixelColor(121, 12) == QColor("black")
    assert node.groups[sprite].texture is texture
    assert len(group.images) <= label_layer._SPARE_IMAGES
    assert _render(surface, [first, second]).pixelColor(121, 12) == QColor("blue")
    assert node.groups[sprite].texture is texture
    assert len(group.images) == 2
    assert _render(surface, [first]).pixelColor(121, 12) == QColor("black")
    _render(surface, [])
    assert not isValid(node)
    assert not isValid(texture)
    assert _render(surface, [first]).pixelColor(21, 12) == QColor("blue")
    assert label_nodes[-1] is not node


@pytest.mark.parametrize("byte_limit", [False, True])
def test_label_cache_evicts_unused_textures_without_evicting_visible_labels(
    surface: QuickSurface, label_nodes: list[_LabelNode], monkeypatch: pytest.MonkeyPatch, byte_limit: bool
) -> None:
    """Both cache limits retire old native textures while retaining every visible texture."""
    monkeypatch.setattr(label_layer, "_SPARE_TEXTURES", 2)
    blue = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    fixed = LabelCall(0.1, 0.1, "top-left", blue)
    changing = replace(fixed, x=0.6, content=replace(blue, background=(255, 0, 0, 255)))
    _render(surface, [fixed, changing])
    node = label_nodes[-1]
    fixed_sprite, old_sprite = node.groups
    fixed_texture, old_texture = (group.texture for group in node.groups.values())
    if byte_limit:
        monkeypatch.setattr(label_layer, "_SPARE_BYTES", old_sprite.image.sizeInBytes() - 1)
    # 40 returns from the hidden cache when only the count limits it, and is rebuilt when bytes do.
    for red in (10, 20, 30, 40, 50, 40, 50):
        current = replace(changing, content=replace(blue, background=(red, 0, 0, 255)))
        rendered = _render(surface, [fixed, current])
        assert rendered.pixelColor(21, 12) == QColor("blue")
        assert rendered.pixelColor(121, 12) == QColor(red, 0, 0)
        hidden = [group for group in node.groups.values() if group.opacity() == 0.0]
        assert node._hidden_bytes == sum(group.byte_count for group in hidden)
    assert node.groups[fixed_sprite].texture is fixed_texture
    assert isValid(fixed_texture)
    assert not isValid(old_texture)
    assert len(node.groups) == (2 if byte_limit else 4)


def test_shared_label_instances_trim_park_and_grow_without_stale_pixels(
    surface: QuickSurface, label_nodes: list[_LabelNode]
) -> None:
    """Trimming borrowed nodes must preserve the owner and hide every removed instance."""
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    first = LabelCall(0.02, 0.1, "top-left", content)
    labels: DrawCalls = [replace(first, x=0.02 + index * 0.12) for index in range(8)]
    _render(surface, labels)
    group = next(iter(label_nodes[-1].groups.values()))
    texture = group.texture
    borrowed = group.images[-1]
    _render(surface, [replace(first, content=replace(content, background=(255, 255, 0, 255)))])
    assert len(group.images) == label_layer._SPARE_IMAGES
    assert not isValid(borrowed)
    assert isValid(texture)
    rendered = _render(surface, labels[:2])
    for index in range(8):
        assert rendered.pixelColor(5 + index * 24, 12) == QColor("blue" if index < 2 else "black")
    rendered = _render(surface, labels)
    assert group.texture is texture
    for index in range(8):
        assert rendered.pixelColor(5 + index * 24, 12) == QColor("blue")


def test_labels_survive_window_reparenting_and_graph_recreation(
    surface: QuickSurface, qtbot: QtBot, label_nodes: list[_LabelNode]
) -> None:
    """Textures belong to one node tree and are rebuilt for the new window's graphics resources."""
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    label = LabelCall(0.1, 0.1, "top-left", content)
    _render(surface, [label])
    texture = next(iter(label_nodes[-1].groups.values())).texture
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(200, 100)
    surface.setParent(host)
    host.show()
    surface.show()
    assert _render(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.cleanup()
    QApplication.processEvents()
    surface.show()
    surface.grabFramebuffer()
    assert not isValid(texture)
    assert _render(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.setParent(None)


def test_label_textures_are_owned_independently_by_each_surface(
    surface: QuickSurface, other_surface: QuickSurface, label_nodes: list[_LabelNode]
) -> None:
    """Clearing one viewer must not invalidate another viewer sharing the same CPU sprite."""
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    label = LabelCall(0.1, 0.1, "top-left", content)
    _render(surface, [label])
    first = next(iter(label_nodes[-1].groups.values())).texture
    _render(other_surface, [label])
    second = next(iter(label_nodes[-1].groups.values())).texture
    assert first is not second
    _render(surface, [])
    assert not isValid(first)
    assert isValid(second)
    assert _render(other_surface, [label]).pixelColor(21, 12) == QColor("blue")


def test_label_tree_survives_garbage_collection_without_python_references(
    surface: QuickSurface, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qt keeps the Python node subclasses alive, so a moving label reuses one texture without held references."""
    created: list[None] = []
    create = label_layer._SpriteGroup.__init__

    def counting(group: label_layer._SpriteGroup, window: QQuickWindow, sprite: LabelSprite) -> None:
        create(group, window, sprite)
        created.append(None)

    monkeypatch.setattr(label_layer._SpriteGroup, "__init__", counting)
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    label = LabelCall(0.1, 0.1, "top-left", content)
    for x in (0.1, 0.3, 0.5, 0.3):
        rendered = _render(surface, [replace(label, x=x)])
        gc.collect()
        assert rendered.pixelColor(round(x * 200) + 1, 12) == QColor("blue")
    assert len(created) == 1


def test_labels_stay_inside_the_image(surface: QuickSurface) -> None:
    """A label placed above a box at the image's top edge, or past its right edge, moves just inside the image."""
    content = LabelContent((LabelRun("38", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    _render(
        surface,
        [LabelCall(0.2, 0.0, "bottom-left", content), LabelCall(1.0, 0.5, "top-left", content)],
    )
    for label in surface._overlays._drawing.labels:
        rect = QRectF(label.position, QSizeF(label.sprite.width, label.sprite.height))
        assert _TARGET.contains(rect), rect
