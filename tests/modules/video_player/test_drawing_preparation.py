"""Prepared output: failed templates, label and text layout, culling and atlas resets."""

from dataclasses import replace

import numpy as np
import pytest
from numpy.typing import NDArray
from PySide6.QtGui import QRawFont

from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.quick import glyphs
from ax_devil.modules.video_player.engine.quick.overlay_vertex import OVERLAY_VERTEX, OverlayKind
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.drawing_helpers import (
    BoxCall,
    CircleCall,
    DrawingStyle,
    LabelCall,
    LabelContent,
    LabelRun,
    LineCall,
    PointCall,
    PolygonCall,
    PolylineCall,
    TextCall,
)

from .conftest import RenderQuads

pytestmark = pytest.mark.usefixtures("qapp")

_STYLE = DrawingStyle(pen_width=0.02, brush_r=255, brush_a=255)
_SETTINGS = DrawingSettings(200, 100, 100)
_TRIANGLE = ((0.1, 0.1), (0.3, 0.1), (0.3, 0.3))


def test_failed_template_discards_all_output_kinds_and_preserves_prior_drawing() -> None:
    """A late failure must leave no geometry, glyphs or stale reuse entries."""
    catalog = RenderProgramCompiler().compile_catalog(
        {
            "main": {
                "parameters": {"divisor": {"type": "number", "required": True}},
                "steps": [
                    {
                        "primitive": "box",
                        "style": {"fill": {"color": [255, 0, 0], "alpha": 255}},
                        "fields": {"geometry": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}},
                    },
                    {
                        "primitive": "point",
                        "style": {
                            "stroke": {
                                "color": [255, 255, 255],
                                "width": {"value": 2, "unit": "px"},
                                "pattern": "solid",
                            }
                        },
                        "fields": {"position": {"x": 0.5, "y": 0.5}},
                    },
                    {
                        "primitive": "text",
                        "style": {"text": {"color": [255, 255, 255], "size": {"value": 12, "unit": "px"}}},
                        "fields": {"position": {"x": 0, "y": 0}, "text": "Partial", "anchor": "top-left"},
                    },
                    {
                        "primitive": "point",
                        "style": {
                            "stroke": {
                                "color": [255, 255, 255],
                                "width": {"value": 2, "unit": "px"},
                                "pattern": "solid",
                            }
                        },
                        "fields": {
                            "position": {
                                "x": {
                                    "call": "div",
                                    "args": {"numerator": 1, "denominator": {"ref": ["parameters", "divisor"]}},
                                },
                                "y": 0,
                            }
                        },
                    },
                ],
            }
        }
    )
    context = RenderContext.create(200, 100)
    for divisor in (1, 0, 1, 0):
        buffer = DrawingBuffer(_SETTINGS)
        CircleCall(0.1, 0.1, 0.05, _STYLE).submit(buffer)
        if divisor == 0:
            with pytest.raises(TemplateRuntimeError, match="zero"):
                catalog.render("main", {"divisor": divisor}, buffer, context=context)
            drawing = buffer.finish()
            assert len(drawing) == 1
        else:
            catalog.render("main", {"divisor": divisor}, buffer, context=context)
            drawing = buffer.finish()
            assert len(drawing) == 5


def _label(*texts: str) -> LabelContent:
    first, *rest = texts
    runs = (LabelRun(first, (255, 255, 255), "semibold"), *(LabelRun(text, (255, 255, 255)) for text in rest))
    return LabelContent(runs, 0.12, background=(14, 16, 20, 205), padding_x=0.08, padding_y=0.03, radius=0.1, gap=0.05)


def _quads(drawing: PreparedDrawing, kind: OverlayKind) -> NDArray[np.void]:
    """The drawing's overlay quads of one kind, in drawing order."""
    quads = drawing.vertices.reshape(-1, 4)
    selected: NDArray[np.void] = quads[quads["kind"][:, 0] == kind]
    return selected


def _shapes(quads: NDArray[np.void]) -> list[tuple[float, float, float, float]]:
    """Left, top, width and height of shape quads, which start at u, v = -1 and are one pixel larger all round."""
    return [(float(q["x"][0] + 1), float(q["y"][0] + 1), float(q["w"][0]), float(q["h"][0])) for q in quads]


def _ink(quads: NDArray[np.void]) -> tuple[float, float, float, float]:
    return float(quads["x"].min()), float(quads["y"].min()), float(quads["x"].max()), float(quads["y"].max())


def test_labels_are_sized_to_their_runs_and_placed_by_their_background_on_whole_pixels() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.2034, 0.5017, "bottom-left", _label("Person", "12")).submit(buffer)
    LabelCall(0.5, 0.5, "top-left", _label("Person", "3f2a9c1e")).submit(buffer)
    (short, long) = _shapes(_quads(buffer.finish(), OverlayKind.ROUNDED_RECT))
    assert long[2] > short[2] > 0
    assert short[3] == long[3]
    assert short[0] == round(0.2034 * 200)
    assert short[1] == round(0.5017 * 100 - short[3])
    assert long[:2] == (100, 50)


def test_label_bars_follow_the_text_and_fill_from_the_left() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    red = (255, 0, 0)
    runs = (LabelRun("12", red), LabelRun("", red, bar=0.5))
    LabelCall(0.1, 0.1, "top-left", LabelContent(runs, 0.12, gap=0.05)).submit(buffer)
    drawing = buffer.finish()
    track, filled = _shapes(_quads(drawing, OverlayKind.ROUNDED_RECT))
    # The bar follows the text's advance by the gap; bitmaps may overhang the advance by a pixel or two.
    assert (
        _ink(_quads(drawing, OverlayKind.GLYPH))[2]
        < track[0]
        <= _ink(_quads(drawing, OverlayKind.GLYPH))[2] + 0.05 * 100 + 2
    )
    assert filled[0] == track[0]
    assert filled[2] == pytest.approx(track[2] / 2)


def test_labels_without_runs_draw_nothing() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.5, 0.5, "top-left", replace(_label("x"), runs=())).submit(buffer)
    assert not len(buffer.finish().vertices)


@pytest.mark.parametrize("dpi", [72, 144, 192])
def test_label_text_keeps_its_pixel_size_at_different_logical_dpi(dpi: int) -> None:
    """Fonts are sized in pixels, so the dpi that resolves them must not change the glyphs."""
    content = LabelContent((LabelRun("MMMM", (255, 255, 255)),), 0.2, family="Arial", padding_x=0.1, padding_y=0.1)

    def ink(settings: DrawingSettings) -> tuple[float, float, float, float]:
        buffer = DrawingBuffer(settings)
        LabelCall(0.1, 0.1, "top-left", content).submit(buffer)
        return _ink(_quads(buffer.finish(), OverlayKind.GLYPH))

    assert ink(replace(_SETTINGS, dpi=dpi)) == pytest.approx(ink(_SETTINGS), abs=1)


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_labels_land_on_whole_device_pixels_and_keep_their_size_at_any_dpr(dpr: float) -> None:
    """Both edges must land on device pixels to keep a bitmap glyph from being filtered, whatever the dpr."""
    content = replace(_label("track00412"), size=0.11, padding_x=0.04, padding_y=0.02)

    def label(ratio: float) -> tuple[float, float, float, float]:
        buffer = DrawingBuffer(replace(_SETTINGS, dpr=ratio))
        LabelCall(0.2034, 0.5017, "center", content).submit(buffer)
        (shape,) = _shapes(_quads(buffer.finish(), OverlayKind.ROUNDED_RECT))
        return shape

    left, top, width, height = label(dpr)
    for edge in (left, top, left + width, top + height):
        assert edge * dpr == pytest.approx(round(edge * dpr))
    assert (width, height) == pytest.approx(label(1.0)[2:], abs=2)


@pytest.mark.parametrize("text", ["j", "jazz"])
def test_unpadded_labels_preserve_all_glyph_ink(text: str) -> None:
    """Negative bearings and overhang must fit even when the catalog requests no padding."""
    content = LabelContent((LabelRun(text, (255, 255, 255), "bold"),), 0.32, "DejaVu Sans", (0, 0, 0, 255))
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.3, 0.3, "top-left", content).submit(buffer)
    drawing = buffer.finish()
    ((left, top, width, height),) = _shapes(_quads(drawing, OverlayKind.ROUNDED_RECT))
    ink_left, ink_top, ink_right, ink_bottom = _ink(_quads(drawing, OverlayKind.GLYPH))
    assert left <= ink_left and top <= ink_top
    assert ink_right <= left + width and ink_bottom <= top + height


def test_baseline_text_sits_on_the_requested_line_however_many_lines_follow() -> None:
    def glyphs_of(text: str) -> NDArray[np.void]:
        buffer = DrawingBuffer(_SETTINGS)
        TextCall(0.3, 0.5, text, DrawingStyle(font_size=0.2)).submit(buffer)
        return _quads(buffer.finish(), OverlayKind.GLYPH)

    one, two = glyphs_of("HH"), glyphs_of("HH\nHH")
    assert _ink(one)[0] >= 0.3 * 200 - 1
    assert _ink(glyphs_of("HHg"))[1] == _ink(one)[1]  # A descender does not lift the line off the baseline.
    assert _ink(two)[1] == _ink(one)[1]  # Later lines hang below the first, which stays put.


def test_every_shape_is_an_overlay_quad_below_the_text() -> None:
    """Polygons and polylines share the quad stream with boxes, circles, lines and points, text last."""
    buffer = DrawingBuffer(_SETTINGS)
    BoxCall(0.1, 0.1, 0.4, 0.4, replace(_STYLE, radius=0.06, pen_style="dash")).submit(buffer)
    CircleCall(0.5, 0.5, 0.1, _STYLE).submit(buffer)
    LineCall(0.1, 0.1, 0.9, 0.9, _STYLE).submit(buffer)
    PointCall(0.5, 0.5, _STYLE).submit(buffer)
    PolygonCall(_TRIANGLE, _STYLE).submit(buffer)
    PolygonCall(((0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)), _STYLE).submit(buffer)
    PolylineCall(_TRIANGLE, _STYLE).submit(buffer)
    TextCall(0.5, 0.5, "Text", _STYLE).submit(buffer)
    drawing = buffer.finish()
    is_glyph = (drawing.vertices["kind"][::4] == OverlayKind.GLYPH).tolist()
    assert any(is_glyph) and is_glyph == sorted(is_glyph)
    assert len(drawing) == 8


def _text_frame(settings: DrawingSettings, *calls: tuple[str, float]) -> PreparedDrawing:
    buffer = DrawingBuffer(settings)
    for text, size in calls:
        TextCall(0.1, 0.5, text, replace(_STYLE, font_size=size)).submit(buffer)
    return buffer.finish()


def test_text_in_fonts_split_by_string_evictions_matches_text_without_evictions_and_tiny_text_draws_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The string cache is cleared while the second font group is shaped; the first group must keep its glyphs."""
    calls = (("AB", 0.1), ("IJ", 0.1), ("CD", 0.2), ("KL", 0.2), ("EF", 0.15))
    # Every text alone shares no cache or font group with the others; group order differs, so compare sorted corners.
    alone = np.concatenate([_text_frame(replace(_SETTINGS, dpi=77), call).vertices for call in calls])
    monkeypatch.setattr(glyphs, "MAX_STRINGS", 3)  # Group one holds two strings, group two's two evict them.
    monkeypatch.setitem(glyphs._ATLASES, (78, 1.0), glyphs.GlyphAtlas(78, 1.0))
    evicting = _text_frame(replace(_SETTINGS, dpi=78), *calls)
    assert len(_quads(evicting, OverlayKind.GLYPH)) == 10

    def corners(vertices: NDArray[np.void]) -> NDArray[np.float32]:
        return np.sort(np.stack([vertices["x"], vertices["y"]], axis=1), axis=0)

    assert np.array_equal(corners(evicting.vertices), corners(alone))
    assert not len(_text_frame(_SETTINGS, ("A", 0.0001)).vertices)


def test_text_in_the_letterbox_is_prepared_with_a_viewport_larger_than_the_target() -> None:
    """Ink outside the image but inside the visible surface must survive culling."""
    content = replace(_label("HEADER"), background=None)

    def glyphs_drawn(viewport: tuple[float, float, float, float] | None) -> int:
        buffer = DrawingBuffer(replace(_SETTINGS, viewport=viewport))
        LabelCall(0.0, 0.0, "bottom-left", content).submit(buffer)
        return len(_quads(buffer.finish(), OverlayKind.GLYPH))

    assert glyphs_drawn((0, -50, 200, 200)) == glyphs_drawn(None) > 0


def test_a_full_atlas_page_resets_mid_frame_and_both_drawings_still_render_their_text(
    monkeypatch: pytest.MonkeyPatch,
    render_quads: RenderQuads,
) -> None:
    first_text = "".join(chr(0x0100 + offset) for offset in range(12))
    second_text = "".join(chr(0x0180 + offset) for offset in range(12))
    settings = replace(_SETTINGS, dpi=91)
    reference = [
        render_quads(drawing.vertices, 200, drawing.page)
        for drawing in (_text_frame(settings, (text, 0.12)) for text in (first_text, second_text))
    ]
    monkeypatch.setattr(glyphs, "PAGE_SIZE", 64)  # The layout reads the same constant, so UVs follow the page.
    monkeypatch.setitem(glyphs._ATLASES, (91, 1.0), glyphs.GlyphAtlas(91, 1.0))
    first = _text_frame(settings, (first_text, 0.12))
    second = _text_frame(settings, (second_text, 0.12))  # Overflows 64 px, so finish resets and lays out again.
    assert second.page != first.page
    for drawing, expected in ((second, reference[1]), (first, reference[0])):  # The cached drawing comes last.
        image = render_quads(drawing.vertices, 200, drawing.page)
        assert image == expected
        assert image != render_quads(np.zeros(0, OVERLAY_VERTEX), 200)


def test_dropping_glyphs_is_only_for_the_frame_that_needs_it_after_a_clean_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame that fits a fresh page after a reset must not leave later frames dropping glyphs instead of resetting."""
    monkeypatch.setattr(glyphs, "PAGE_SIZE", 64)
    settings = replace(_SETTINGS, dpi=93)

    def drawn(text: str) -> int:
        """Glyphs of *text* drawn alone on a fresh page, which drops what does not fit."""
        monkeypatch.setitem(glyphs._ATLASES, (93, 1.0), glyphs.GlyphAtlas(93, 1.0))
        return len(_quads(_text_frame(settings, (text, 0.3)), OverlayKind.GLYPH))

    def chars(base: int, count: int) -> str:
        return "".join(chr(base + offset) for offset in range(count))

    def fitting(base: int) -> int:
        """The most leading glyphs of the block at *base* that one fresh page holds, measured as they are drawn."""
        count = 1
        while drawn(chars(base, count + 1)) == count + 1:
            count += 1
            assert count < 12  # The page must be small enough for the test to mean anything.
        return count

    bases = (0x0100, 0x0180, 0x0200)
    fits = min(fitting(base) for base in bases)
    frames = [chars(base, fits) for base in bases]
    assert fits >= 1 and drawn(frames[0] + frames[1]) < 2 * fits  # Two frames never fit one page together.
    monkeypatch.setitem(glyphs._ATLASES, (93, 1.0), glyphs.GlyphAtlas(93, 1.0))
    assert [len(_quads(_text_frame(settings, (text, 0.3)), OverlayKind.GLYPH)) for text in frames] == [fits] * 3


def test_text_that_cannot_fit_one_atlas_page_draws_the_glyphs_that_fit_and_the_next_frame_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(glyphs, "PAGE_SIZE", 64)
    monkeypatch.setitem(glyphs._ATLASES, (92, 1.0), glyphs.GlyphAtlas(92, 1.0))
    settings = replace(_SETTINGS, dpi=92)
    crowded = "".join(chr(0x0100 + offset) for offset in range(30))
    drawing = _text_frame(settings, (crowded, 0.3))
    drawn = len(_quads(drawing, OverlayKind.GLYPH))
    assert 0 < drawn < 30
    assert len(_quads(_text_frame(settings, ("ab", 0.1)), OverlayKind.GLYPH)) == 2


def test_a_label_outside_the_culling_area_is_not_counted() -> None:
    def frame(x: float) -> PreparedDrawing:
        buffer = DrawingBuffer(replace(_SETTINGS, viewport=(0, 0, 200, 100)))
        LabelCall(x, 0.5, "top-left", _label("Person")).submit(buffer)
        return buffer.finish()

    assert len(frame(0.1)) == 1
    outside = frame(5.0)
    assert len(outside) == 0 and not len(outside.vertices)


def test_a_label_font_larger_than_the_atlas_page_is_not_rasterized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glyphs, "PAGE_SIZE", 64)
    monkeypatch.setitem(glyphs._ATLASES, (93, 1.0), glyphs.GlyphAtlas(93, 1.0))
    rasterized: list[int] = []
    original = QRawFont.alphaMapForGlyph

    def spy(self: QRawFont, *args: object) -> object:
        rasterized.append(1)
        return original(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(QRawFont, "alphaMapForGlyph", spy)
    buffer = DrawingBuffer(replace(_SETTINGS, dpi=93))
    LabelCall(0.1, 0.5, "top-left", replace(_label("Person"), size=5.0)).submit(buffer)
    drawing = buffer.finish()
    assert not rasterized and not len(_quads(drawing, OverlayKind.GLYPH))
