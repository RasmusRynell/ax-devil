"""Retained label textures owned entirely by Qt's render-thread node tree."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import cast

from PySide6.QtCore import QPointF
from PySide6.QtQuick import (
    QQuickItem,
    QQuickWindow,
    QSGImageNode,
    QSGNode,
    QSGOpacityNode,
    QSGTexture,
    QSGTransformNode,
)
from shiboken6 import delete

from ..drawing import LabelSprite
from .preparation import LabelData

_SPARE_TEXTURES = 1024
_SPARE_BYTES = 8 * 1024 * 1024
_SPARE_IMAGES = 4


class _SpriteGroup(QSGOpacityNode):
    """One texture owner and any number of image nodes borrowing that texture."""

    def __init__(self, window: QQuickWindow, sprite: LabelSprite) -> None:
        super().__init__()
        self.sprite = sprite
        self.byte_count = sprite.image.sizeInBytes()
        self.texture = window.createTextureFromImage(sprite.image, QQuickWindow.CreateTextureOption.TextureCanUseAtlas)
        self.images: list[QSGImageNode] = []
        self.positions: list[QPointF] = []
        self._append_image(window)
        self.images[0].setOwnsTexture(True)

    def _append_image(self, window: QQuickWindow) -> None:
        image = window.createImageNode()
        if image is None:
            raise RuntimeError("Could not create a label image node.")
        image.setTexture(self.texture)
        image.setFiltering(QSGTexture.Filtering.Linear)
        self.appendChildNode(image)
        self.images.append(image)

    def place(self, window: QQuickWindow, positions: list[QPointF]) -> None:
        """Move existing images and grow only when another label needs the same texture."""
        self.setOpacity(1.0)
        while len(self.images) < len(positions):
            self._append_image(window)
        self._trim(len(positions) + _SPARE_IMAGES)
        for index, position in enumerate(positions):
            if index >= len(self.positions) or position != self.positions[index]:
                self.images[index].setRect(position.x(), position.y(), self.sprite.width, self.sprite.height)
        for index in range(len(positions), min(len(self.positions), len(self.images))):
            self.images[index].setRect(0, 0, 0, 0)
        self.positions = positions

    def park(self) -> None:
        """Keep a bounded set of hidden instances until this content is needed again."""
        self.setOpacity(0.0)
        self._trim(_SPARE_IMAGES)
        self.positions = self.positions[:_SPARE_IMAGES]

    def _trim(self, count: int) -> None:
        while len(self.images) > count:
            image = self.images.pop()
            self.removeChildNode(image)
            delete(image)


class _LabelNode(QSGTransformNode):
    """Cache recently used sprite groups without retaining a frame history or external GPU resources.

    ``groups`` runs from least to most recently shown, so every hidden group comes before the visible ones. Each frame
    costs work for the labels shown and the groups whose visibility changed, not for every cached group.
    """

    def __init__(self) -> None:
        super().__init__()
        # Software rendering caches inherited state on transform/opacity nodes, not plain QSGNodes.
        # An identity transform keeps new and returning sprite groups in the layer's coordinate space.
        self.groups: OrderedDict[LabelSprite, _SpriteGroup] = OrderedDict()
        self._shown: set[LabelSprite] = set()
        self._hidden_bytes = 0

    def present(self, window: QQuickWindow, labels: tuple[LabelData, ...]) -> None:
        """Share textures within this layer and retire unused groups on the render thread."""
        positions: defaultdict[LabelSprite, list[QPointF]] = defaultdict(list)
        for label in labels:
            positions[label.sprite].append(label.position)
        for sprite, points in positions.items():
            group = self.groups.get(sprite)
            if group is None:
                group = _SpriteGroup(window, sprite)
                self.groups[sprite] = group
                self.appendChildNode(group)
            elif sprite not in self._shown:
                self._hidden_bytes -= group.byte_count
            self.groups.move_to_end(sprite)
            group.place(window, points)
        for sprite in self._shown.difference(positions):
            group = self.groups[sprite]
            group.park()
            self._hidden_bytes += group.byte_count
        self._shown = set(positions)
        while len(self.groups) - len(self._shown) > _SPARE_TEXTURES or self._hidden_bytes > _SPARE_BYTES:
            _sprite, group = self.groups.popitem(last=False)
            self._hidden_bytes -= group.byte_count
            self.removeChildNode(group)
            delete(group)


class LabelLayer(QQuickItem):
    """Submit labels as one item; Qt owns every texture through the returned node tree."""

    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents)
        self._labels: tuple[LabelData, ...] = ()

    def set_labels(self, labels: tuple[LabelData, ...]) -> None:
        """Queue final label images and positions without touching render-thread resources."""
        if labels != self._labels:
            self._labels = labels
            self.update()

    def updatePaintNode(  # type: ignore[override]  # Qt permits null nodes.  # noqa: N802
        self, old_node: QSGNode | None, data: QQuickItem.UpdatePaintNodeData
    ) -> QSGNode | None:
        """Reuse sprite textures, or release the entire tree when labels are cleared."""
        window = self.window()
        if window is None or not self._labels:
            if old_node is not None:
                delete(old_node)
            return None
        node = cast(_LabelNode, old_node) if old_node is not None else _LabelNode()
        node.present(window, self._labels)
        return node
