"""Text and label contracts of the native Quick backend."""

from __future__ import annotations

import gc
from dataclasses import replace

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.video_player.engine.drawing import (
    LabelContent,
    LabelRun,
    LabelSprite,
)
from ax_devil.modules.video_player.engine.quick import labels as label_layer
from ax_devil.modules.video_player.engine.quick.items import TextItem
from ax_devil.modules.video_player.engine.quick.labels import _LabelNode
from ax_devil.modules.video_player.engine.quick.prepared import LabelData
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from tests.drawing_helpers import (
    BoxCall,
    DrawCalls,
    LabelCall,
    TextCall,
    prepare_calls,
)
from tests.helpers.quick_surface import (
    FILL,
    TARGET,
    WHITE,
    render_calls,
    solid_image,
)


def test_multiline_text_draws_above_geometry_in_any_emission_order(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Hello\nWorld", WHITE, anchor="top-left")
    rendered = render_calls(surface, [text])
    lit_top = sum(rendered.pixelColor(x, y).red() > 100 for y in range(10, 30) for x in range(20, 100))
    lit_bottom = sum(rendered.pixelColor(x, y).red() > 100 for y in range(30, 60) for x in range(20, 100))
    assert lit_top > 30
    assert lit_bottom > 30
    box = BoxCall(0, 0, 1, 1, FILL)
    orders: list[DrawCalls] = [[text, box], [box, text]]
    for calls in orders:
        visible = render_calls(surface, calls)
        assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 60) for x in range(20, 100))
        assert visible.pixelColor(190, 90) == QColor("red")


def test_moving_text_updates_position_and_replaces_glyphs(surface: QuickSurface) -> None:
    text = TextCall(0.1, 0.1, "Wide", WHITE, anchor="top-left")
    first = render_calls(surface, [text])
    text = replace(text, x=0.6)
    moved = render_calls(surface, [text])
    assert any(first.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(20, 70))
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(20, 70))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(120, 175))
    text = replace(text, text="Other")
    assert render_calls(surface, [text]) != moved


def test_independent_text_movement_color_and_slot_replacement(surface: QuickSurface) -> None:
    """Consecutive labels move separately and replaced slots leave no stale glyphs."""
    first = TextCall(0.05, 0.1, "Left", WHITE, anchor="top-left")
    second = TextCall(0.55, 0.1, "Right", WHITE, anchor="top-left")
    original = render_calls(surface, [first, second])
    moved = render_calls(surface, [first, replace(second, y=0.6, style=replace(WHITE, pen_g=0, pen_b=0))])
    assert original.copy(0, 0, 100, 100) == moved.copy(0, 0, 100, 100)
    assert all(moved.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(110, 190))
    assert any(moved.pixelColor(x, y).red() > 100 for y in range(60, 85) for x in range(110, 190))
    covered = render_calls(surface, [BoxCall(0, 0, 1, 1, FILL)])
    assert covered.convertToFormat(QImage.Format.Format_RGB32) == solid_image("red")
    assert render_calls(surface, [first, second]) == original


def shown_text_items(surface: QuickSurface) -> dict[tuple[float, float], TextItem]:
    root = surface.rootObject()
    assert root is not None
    items = root.findChildren(TextItem)
    return {(item.x(), item.y()): item for item in items if item.isVisible()}


def test_removing_a_label_keeps_remaining_labels_in_place(surface: QuickSurface) -> None:
    """Earlier removals must not shift later labels onto the wrong retained items."""
    labels: DrawCalls = [TextCall(0.05 + 0.3 * i, 0.1, name, WHITE, anchor="top-left") for i, name in enumerate("ABC")]
    original = render_calls(surface, labels)
    original_items = shown_text_items(surface)
    remaining = render_calls(surface, labels[1:])
    # Moving surviving labels onto other native items rebuilds their glyphs on every change: the guarded regression.
    remaining_items = shown_text_items(surface)
    assert len(remaining_items) == 2
    assert all(original_items.get(position) is item for position, item in remaining_items.items())
    assert remaining.copy(60, 0, 140, 100) == original.copy(60, 0, 140, 100)
    assert all(remaining.pixelColor(x, y) == QColor("black") for y in range(10, 35) for x in range(5, 50))
    assert any(remaining.pixelColor(x, y).red() > 100 for y in range(10, 35) for x in range(65, 110))


def test_reordering_keeps_text_visible_on_its_items_and_pools_bounded(surface: QuickSurface) -> None:
    box = BoxCall(0, 0, 1, 1, FILL)
    text = TextCall(0.1, 0.1, "Visible", WHITE, anchor="top-left")
    render_calls(surface, [text, box])
    visible = render_calls(surface, [box, text])
    assert any(visible.pixelColor(x, y).green() > 100 for y in range(10, 40) for x in range(20, 100))
    covered = render_calls(surface, [text, box])
    assert any(covered.pixelColor(x, y).green() > 100 for y in range(10, 40) for x in range(20, 100))

    labels: DrawCalls = [TextCall(0.05 + 0.3 * i, 0.5, name, WHITE, anchor="top-left") for i, name in enumerate("ABC")]
    render_calls(surface, labels)
    original_items = shown_text_items(surface)
    render_calls(surface, labels[1:] + labels[:1])
    # Reordered labels must keep their native items; rebuilding glyphs on every change is the guarded regression.
    reordered_items = shown_text_items(surface)
    assert reordered_items.keys() == original_items.keys()
    assert all(original_items[position] is item for position, item in reordered_items.items())

    # Scene-graph items are private, but their count is the only observation of a shrinking-load leak.
    render_calls(surface, [box] * 80)
    render_calls(surface, [box])
    assert len(surface._overlays._paths.items) <= 33
    assert len(surface._overlays._geometry) <= 33
    assert len(surface._overlays._texts.items) <= 32
    render_calls(surface, [])
    assert not surface._overlays._paths.items and not surface._overlays._texts.items and not surface._overlays._geometry


def test_label_draws_its_background_above_geometry_and_moves_without_repainting(surface: QuickSurface) -> None:
    content = LabelContent((LabelRun("Person", (255, 255, 255), "semibold"),), 0.12, background=(0, 0, 255, 255))
    label = LabelCall(0.1, 0.1, "top-left", replace(content, padding_x=0.06, padding_y=0.04))
    rendered = render_calls(surface, [BoxCall(0, 0, 1, 1, FILL), label])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(190, 90) == QColor("red")
    sprite = surface._overlays._drawing.labels[0].sprite
    moved = render_calls(surface, [replace(label, x=0.5)])
    assert moved.pixelColor(22, 12) == QColor("black")
    assert moved.pixelColor(102, 12) == QColor("blue")
    assert surface._overlays._drawing.labels[0].sprite is sprite


@pytest.mark.parametrize("origin", [(20, 10), (-20, -10)])
@pytest.mark.parametrize("opacity", [1.0, 0.5])
def test_new_and_returning_labels_keep_viewport_transform_and_opacity(
    surface: QuickSurface, origin: tuple[int, int], opacity: float
) -> None:
    """Changing label content after rendering preserves the video origin and overlay opacity."""
    content = LabelContent(
        (LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_x=0.06, padding_y=0.04
    )
    target = TARGET.translated(*origin)
    for text in ("A", "B", "A"):
        calls: DrawCalls = [
            BoxCall(0.4, 0.4, 0.2, 0.2, FILL),
            LabelCall(0.4, 0.4, "top-left", replace(content, runs=(LabelRun(text, (255, 255, 255)),))),
        ]
        surface.set_frame_image(solid_image(), target)
        surface.set_overlays(prepare_calls(calls, surface.drawing_buffer(target, 100)), opacity, target, 100, None)
        QApplication.processEvents()
        rendered = surface.grabFramebuffer()
        dpr = rendered.devicePixelRatio()
        color = rendered.pixelColor(round((82 + origin[0]) * dpr), round((42 + origin[1]) * dpr))
        assert abs(color.blue() - round(255 * opacity)) <= 1
        assert abs(color.red() - round(255 * opacity * (1 - opacity))) <= 1


def test_label_texture_replacement_preserves_neighboring_images(surface: QuickSurface) -> None:
    """Distinct label images keep their own pixels when one changes or releases its atlas allocation.

    The software backend has no atlas; run this on OpenGL as ``docs/runbooks/testing.md`` describes to cover it.
    """
    content = LabelContent(
        (LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_x=0.06, padding_y=0.04
    )
    first = LabelCall(0.1, 0.1, "top-left", content)
    second = LabelCall(0.6, 0.1, "top-left", replace(content, background=(0, 255, 0, 255)))
    rendered = render_calls(surface, [first, second])
    assert rendered.pixelColor(22, 12) == QColor("blue")
    assert rendered.pixelColor(122, 12) == QColor(0, 255, 0)

    changed = replace(first, content=replace(content, background=(255, 255, 0, 255)))
    rendered = render_calls(surface, [changed, second])
    assert rendered.pixelColor(22, 12) == QColor("yellow")
    assert rendered.pixelColor(122, 12) == QColor(0, 255, 0)
    rendered = render_calls(surface, [second])
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
    rendered = render_calls(surface, [first, second])
    assert rendered.pixelColor(21, 12) == rendered.pixelColor(121, 12) == QColor("blue")
    node = label_nodes[-1]
    ((sprite, group),) = node.groups.items()
    texture = group.texture
    assert len(group.images) == 2
    changed = replace(first, content=replace(blue, background=(255, 255, 0, 255)))
    rendered = render_calls(surface, [changed])
    assert rendered.pixelColor(21, 12) == QColor("yellow")
    assert rendered.pixelColor(121, 12) == QColor("black")
    assert node.groups[sprite].texture is texture
    assert len(group.images) <= label_layer._SPARE_IMAGES
    assert render_calls(surface, [first, second]).pixelColor(121, 12) == QColor("blue")
    assert node.groups[sprite].texture is texture
    assert len(group.images) == 2
    assert render_calls(surface, [first]).pixelColor(121, 12) == QColor("black")
    render_calls(surface, [])
    assert not isValid(node)
    assert not isValid(texture)
    assert render_calls(surface, [first]).pixelColor(21, 12) == QColor("blue")
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
    render_calls(surface, [fixed, changing])
    node = label_nodes[-1]
    fixed_sprite, old_sprite = node.groups
    fixed_texture, old_texture = (group.texture for group in node.groups.values())
    if byte_limit:
        monkeypatch.setattr(label_layer, "_SPARE_BYTES", old_sprite.image.sizeInBytes() - 1)
    # 40 returns from the hidden cache when only the count limits it, and is rebuilt when bytes do.
    for red in (10, 20, 30, 40, 50, 40, 50):
        current = replace(changing, content=replace(blue, background=(red, 0, 0, 255)))
        rendered = render_calls(surface, [fixed, current])
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
    render_calls(surface, labels)
    group = next(iter(label_nodes[-1].groups.values()))
    texture = group.texture
    borrowed = group.images[-1]
    render_calls(surface, [replace(first, content=replace(content, background=(255, 255, 0, 255)))])
    assert len(group.images) == label_layer._SPARE_IMAGES
    assert not isValid(borrowed)
    assert isValid(texture)
    rendered = render_calls(surface, labels[:2])
    for index in range(8):
        assert rendered.pixelColor(5 + index * 24, 12) == QColor("blue" if index < 2 else "black")
    rendered = render_calls(surface, labels)
    assert group.texture is texture
    for index in range(8):
        assert rendered.pixelColor(5 + index * 24, 12) == QColor("blue")


def test_labels_survive_window_reparenting_and_graph_recreation(
    surface: QuickSurface, qtbot: QtBot, label_nodes: list[_LabelNode]
) -> None:
    """Textures belong to one node tree and are rebuilt for the new window's graphics resources."""
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    label = LabelCall(0.1, 0.1, "top-left", content)
    render_calls(surface, [label])
    texture = next(iter(label_nodes[-1].groups.values())).texture
    host = QWidget()
    qtbot.addWidget(host)
    host.resize(200, 100)
    surface.setParent(host)
    host.show()
    surface.show()
    assert render_calls(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.cleanup()
    QApplication.processEvents()
    surface.show()
    surface.grabFramebuffer()
    assert not isValid(texture)
    assert render_calls(surface, [label]).pixelColor(21, 12) == QColor("blue")
    surface.setParent(None)


def test_label_textures_are_owned_independently_by_each_surface(
    surface: QuickSurface, other_surface: QuickSurface, label_nodes: list[_LabelNode]
) -> None:
    """Clearing one viewer must not invalidate another viewer sharing the same CPU sprite."""
    content = LabelContent((LabelRun("A", (255, 255, 255)),), 0.12, background=(0, 0, 255, 255), padding_y=0.04)
    label = LabelCall(0.1, 0.1, "top-left", content)
    render_calls(surface, [label])
    first = next(iter(label_nodes[-1].groups.values())).texture
    render_calls(other_surface, [label])
    second = next(iter(label_nodes[-1].groups.values())).texture
    assert first is not second
    render_calls(surface, [])
    assert not isValid(first)
    assert isValid(second)
    assert render_calls(other_surface, [label]).pixelColor(21, 12) == QColor("blue")


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
        rendered = render_calls(surface, [replace(label, x=x)])
        gc.collect()
        assert rendered.pixelColor(round(x * 200) + 1, 12) == QColor("blue")
    assert len(created) == 1
