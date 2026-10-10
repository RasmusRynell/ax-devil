"""Transactional prepared output, retained reuse and complete target invalidation."""

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtGui import QImage

from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.drawing import DrawingStyle, LabelContent, LabelRun, Paint, label_sprite
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.drawing_helpers import BoxCall, CircleCall, LabelCall, TextCall

pytestmark = pytest.mark.usefixtures("qapp")

_STYLE = DrawingStyle(pen_width=0.02, brush_r=255, brush_a=255)
_SETTINGS = DrawingSettings(200, 100, 100, hardware=True)


def test_failed_template_discards_all_output_kinds_and_preserves_prior_drawing() -> None:
    """A late failure must leave no geometry, glyphs, counts or stale reuse entries."""
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
    buffer = DrawingBuffer(_SETTINGS)
    context = RenderContext.create(200, 100)
    for divisor in (1, 0, 1, 0):
        buffer.reset(_SETTINGS)
        CircleCall(0.1, 0.1, 0.05, _STYLE).submit(buffer)
        if divisor == 0:
            with pytest.raises(TemplateRuntimeError, match="zero"):
                catalog.render("main", {"divisor": divisor}, buffer, context=context)
            drawing = buffer.finish()
            assert drawing.counts == (("circle", 1),)
            assert not drawing.meshes and not drawing.texts
            assert len(drawing.paths) == 1
        else:
            catalog.render("main", {"divisor": divisor}, buffer, context=context)
            drawing = buffer.finish()
            assert len(drawing) == 5
            assert len(drawing.texts) == 1
            assert sum(count for _, count in drawing.counts) == 5


def test_retained_preparation_reuses_unchanged_output_and_invalidates_culling() -> None:
    """One-frame reuse must survive a neighbor update without hiding newly visible data."""
    settings = replace(_SETTINGS, viewport=(0, 0, 200, 100))
    buffer = DrawingBuffer(settings)
    BoxCall(0.1, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    TextCall(0.5, 0.5, "Stable", _STYLE).submit(buffer)
    BoxCall(2, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    first = buffer.finish()
    assert len(first) == 2
    buffer.reset(settings)
    BoxCall(0.2, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    TextCall(0.5, 0.5, "Stable", _STYLE).submit(buffer)
    BoxCall(2, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    second = buffer.finish()
    assert second.texts[0].block is first.texts[0].block
    assert second.meshes[0].payload != first.meshes[0].payload
    buffer.reset(replace(settings, viewport=(0, 0, 600, 100)))
    BoxCall(0.2, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    TextCall(0.5, 0.5, "Stable", _STYLE).submit(buffer)
    BoxCall(2, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    third = buffer.finish()
    assert len(third) == 3
    assert len(first) == len(second) == 2
    assert [mesh.operation_count for mesh in third.meshes] == [2]


def test_labels_between_geometry_do_not_split_its_batch() -> None:
    """Geometry is one layer below text, so emitting text between boxes keeps them in one batch."""
    buffer = DrawingBuffer(_SETTINGS)
    BoxCall(0.1, 0.1, 0.1, 0.1, _STYLE).submit(buffer)
    TextCall(0.5, 0.5, "keep", _STYLE).submit(buffer)
    BoxCall(0.2, 0.2, 0.1, 0.1, _STYLE).submit(buffer)
    drawing = buffer.finish()
    assert [mesh.operation_count for mesh in drawing.meshes] == [2]
    assert [layout.text() for text in drawing.texts for layout in text.block.layouts] == ["keep"]


def test_bulk_geometry_beyond_one_batch_keeps_every_row() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    count = 10_000
    x = np.arange(count) / count
    buffer.boxes(x, np.full(count, 0.1), np.full(count, 0.1), np.full(count, 0.1), Paint.of(_STYLE))
    drawing = buffer.finish()
    assert len(drawing.meshes) > 1
    assert sum(mesh.operation_count for mesh in drawing.meshes) == count


def test_moving_labels_keep_live_layouts_beyond_the_inactive_cache_capacity() -> None:
    """An active working set must not reshape itself when its positions change."""
    buffer = DrawingBuffer(_SETTINGS)
    for i in range(1500):
        TextCall(0.1, 0.1, f"Label {i}", _STYLE).submit(buffer)
    first = buffer.finish()
    buffer.reset(_SETTINGS)
    for i in range(1500):
        TextCall(0.2, 0.2, f"Label {i}", _STYLE).submit(buffer)
    second = buffer.finish()
    assert len(first.texts) == len(second.texts) == 1500
    assert all(a.block is b.block for a, b in zip(first.texts, second.texts))
    assert all(a.position != b.position for a, b in zip(first.texts, second.texts))


def test_reused_vertex_storage_cannot_mutate_a_published_drawing() -> None:
    """Qt may consume an earlier drawing after the scratch buffer has been refilled."""
    buffer = DrawingBuffer(_SETTINGS)
    BoxCall(0.1, 0.1, 0.2, 0.2, _STYLE).submit(buffer)
    first = buffer.finish()
    original = first.meshes[0].payload
    buffer.reset(_SETTINGS)
    for index in range(1000):
        BoxCall(index / 10000, 0.2, 0.2, 0.2, _STYLE).submit(buffer)
    second = buffer.finish()
    assert first.meshes[0].payload == original
    assert second.meshes[0].payload != original


def _label(*texts: str) -> LabelContent:
    first, *rest = texts
    runs = (LabelRun(first, (255, 255, 255), "semibold"), *(LabelRun(text, (255, 255, 255)) for text in rest))
    return LabelContent(runs, 0.12, background=(14, 16, 20, 205), padding_x=0.08, padding_y=0.03, radius=0.1, gap=0.05)


def test_labels_are_sized_to_their_runs_and_placed_by_their_background_on_whole_pixels() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.2034, 0.5017, "bottom-left", _label("Person", "12")).submit(buffer)
    LabelCall(0.5, 0.5, "top-left", _label("Person", "3f2a9c1e")).submit(buffer)
    LabelCall(0.7, 0.5, "top-left", _label("Person", "12")).submit(buffer)
    short, long, again = buffer.finish().labels
    assert long.sprite.width > short.sprite.width > 0
    assert short.sprite.height == long.sprite.height
    assert again.sprite is short.sprite
    assert short.position.x() == round(0.2034 * 200)
    assert short.position.y() == round(0.5017 * 100 - short.sprite.height)
    assert (long.position.x(), long.position.y()) == (100, 50)


def test_label_bars_follow_the_text_and_fill_from_the_left() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    red = (255, 0, 0)
    for runs in ((LabelRun("12", red),), (LabelRun("12", red), LabelRun("", red, bar=0.5))):
        LabelCall(0.1, 0.1, "top-left", LabelContent(runs, 0.12, gap=0.05)).submit(buffer)
    text, both = buffer.finish().labels
    start = text.sprite.width + 0.05 * 100
    length = both.sprite.width - start
    assert length > 0
    image, ratio = both.sprite.image, both.sprite.image.devicePixelRatio()
    row = round(both.sprite.height / 2 * ratio)

    def alpha(at: float) -> int:
        return image.pixelColor(round((start + at * length) * ratio), row).alpha()

    assert alpha(0.25) == 255
    assert 0 < alpha(0.75) < 255


def test_labels_without_runs_draw_nothing() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.5, 0.5, "top-left", replace(_label("x"), runs=())).submit(buffer)
    assert buffer.finish().labels == ()


@pytest.mark.parametrize("dpi", [72, 144, 192])
def test_label_text_keeps_its_pixel_size_at_different_logical_dpi(dpi: int) -> None:
    """Measurement and painting must agree on the size and position of the glyphs."""
    content = LabelContent((LabelRun("MMMM", (255, 255, 255)),), 0.2, family="Arial", padding_x=0.1, padding_y=0.1)
    reference = label_sprite(content, 100, 96, 1)
    actual = label_sprite(content, 100, dpi, 1)
    assert reference is not None and actual is not None

    def ink_bounds(image: QImage) -> tuple[int, int, int, int]:
        pixels = [(x, y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha()]
        return (
            min(x for x, _ in pixels),
            min(y for _, y in pixels),
            max(x for x, _ in pixels),
            max(y for _, y in pixels),
        )

    assert ink_bounds(actual.image) == ink_bounds(reference.image)


def test_moving_labels_keep_live_sprites_beyond_the_inactive_cache_capacity() -> None:
    """An unchanged visible label must keep its pixels and texture even after LRU eviction."""
    buffer = DrawingBuffer(_SETTINGS)
    for index in range(1025):
        LabelCall(0.1, 0.1, "top-left", _label(f"Object {index}")).submit(buffer)
    first = buffer.finish()
    buffer.reset(_SETTINGS)
    for index in range(1025):
        LabelCall(0.2, 0.2, "top-left", _label(f"Object {index}")).submit(buffer)
    second = buffer.finish()
    assert len(first.labels) == len(second.labels) == 1025
    assert all(a.sprite is b.sprite for a, b in zip(first.labels, second.labels))
    assert all(a.position != b.position for a, b in zip(first.labels, second.labels))


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_label_rasters_are_placed_without_fractional_pixel_rescaling(dpr: float) -> None:
    """Both edges must land on device pixels to keep a rasterized glyph from being filtered again."""
    buffer = DrawingBuffer(replace(_SETTINGS, dpr=dpr))
    content = LabelContent((LabelRun("track00412", (255, 255, 255)),), 0.11, padding_x=0.04, padding_y=0.02)
    LabelCall(0.2034, 0.5017, "center", content).submit(buffer)
    (label,) = buffer.finish().labels
    sprite = label.sprite
    assert sprite.width * dpr == sprite.image.width()
    assert sprite.height * dpr == sprite.image.height()
    for edge in (
        label.position.x(),
        label.position.y(),
        label.position.x() + sprite.width,
        label.position.y() + sprite.height,
    ):
        assert edge * dpr == pytest.approx(round(edge * dpr))


@pytest.mark.parametrize("text", ["j", "jazz"])
def test_unpadded_labels_preserve_all_glyph_ink(text: str) -> None:
    """Negative bearings and overhang must fit even when the catalog requests no padding."""
    content = LabelContent((LabelRun(text, (255, 255, 255), "bold"),), 0.32, family="DejaVu Sans")
    plain = label_sprite(content, 100, 96, 1)
    padded = label_sprite(replace(content, padding_x=0.1, padding_y=0.1), 100, 96, 1)
    assert plain is not None and padded is not None

    def ink(image: QImage) -> int:
        return sum(image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width()))

    assert ink(plain.image) == ink(padded.image)


@pytest.mark.parametrize("padding", [1e308, 1e10, (2**31 - 0.5 - 30) / 200])
def test_unrepresentable_label_rasters_do_not_discard_adjacent_labels(padding: float) -> None:
    """Finite style lengths can overflow layout or exceed QImage's integer dimensions."""
    buffer = DrawingBuffer(_SETTINGS)
    LabelCall(0.1, 0.1, "top-left", _label("Visible")).submit(buffer)
    oversized = LabelContent((LabelRun("", (255, 255, 255), bar=0.5),), 0.12, padding_x=padding)
    LabelCall(0.2, 0.2, "top-left", oversized).submit(buffer)
    drawing = buffer.finish()
    assert len(drawing.labels) == 1
    assert drawing.labels[0].position.x() == 20


def test_rounded_boxes_are_paths_with_arcs_even_on_hardware() -> None:
    buffer = DrawingBuffer(_SETTINGS)
    BoxCall(0.1, 0.1, 0.4, 0.4, replace(_STYLE, radius=0.06)).submit(buffer)
    BoxCall(0.6, 0.1, 0.3, 0.3, _STYLE).submit(buffer)
    drawing = buffer.finish()
    assert [" a " in path.geometry for path in drawing.paths] == [True]
    assert sum(mesh.operation_count for mesh in drawing.meshes) == 1
