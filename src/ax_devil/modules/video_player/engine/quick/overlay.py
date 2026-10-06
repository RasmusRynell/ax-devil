"""Apply final prepared drawing data to retained native Qt slots."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Sequence
from pathlib import Path
from typing import Generic, TypeVar, cast

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlListReference
from PySide6.QtQuick import QQuickItem

from .geometry import GeometryItem
from .items import TextItem
from .labels import LabelLayer
from .preparation import EMPTY_DRAWING, PathData, PreparedDrawing, TextData

_Item = TypeVar("_Item")
_Record = TypeVar("_Record", PathData, TextData)
_SPARE = 32
# Each kind is one layer; text and labels draw above shapes so crowded scenes stay readable.
_MESH_Z, _PATH_Z, _TEXT_Z, _LABEL_Z = 0, 1, 2, 3


class ShapeComponents:
    """Compile the two small QML geometry adapters once per display engine."""

    def __init__(self, engine: QQmlEngine) -> None:
        directory = Path(__file__).parent
        self.shape = QQmlComponent(engine, QUrl.fromLocalFile(str(directory / "Shape.qml")))
        self.path = QQmlComponent(engine, QUrl.fromLocalFile(str(directory / "Path.qml")))
        for component in (self.shape, self.path):
            if component.isError():
                raise RuntimeError(f"Quick shape component failed: {component.errors()}")


class _PathItem:
    def __init__(self, parent: QQuickItem, components: ShapeComponents) -> None:
        item = components.shape.create()
        if item is None:
            raise RuntimeError(f"Quick shape creation failed: {components.shape.errors()}")
        self.item = cast(QQuickItem, item)
        self.item.setParent(parent)
        self.item.setParentItem(parent)
        self.item.setZ(_PATH_Z)
        obj = components.path.create()
        if obj is None:
            self.item.deleteLater()
            raise RuntimeError(f"Quick path creation failed: {components.path.errors()}")
        obj.setParent(self.item)
        paths = QQmlListReference(self.item, "data")  # type: ignore[call-overload]  # Binding requires str.
        paths.append(obj)
        self._object = obj
        self._data: PathData | None = None

    def update(self, data: PathData) -> None:
        self.item.setPosition(data.position)
        previous = self._data
        for name, value, changed in (
            ("geometry", data.geometry, previous is None or data.geometry != previous.geometry),
            ("strokeColor", data.stroke, previous is None or data.stroke != previous.stroke),
            ("fillColor", data.fill, previous is None or data.fill != previous.fill),
            ("strokeWidth", data.width, previous is None or data.width != previous.width),
            ("dashed", data.dashed, previous is None or data.dashed != previous.dashed),
        ):
            if changed:
                self._object.setProperty(name, value)
        self._data = data
        self.item.ensurePolished()


def _retire(item: QQuickItem) -> None:
    item.setVisible(False)
    item.deleteLater()


def _path_content(path: PathData) -> Hashable:
    return path.geometry, path.stroke.rgba(), path.fill.rgba(), path.width, path.dashed


def _text_content(text: TextData) -> Hashable:
    # Text blocks are interned while an item displays them, so identity is content identity.
    return id(text.block), text.color.rgba()


class _ContentSlots(Generic[_Item, _Record]):
    """Retained items matched to records by content rather than by position in the drawing.

    Removing, adding or reordering records therefore never rebuilds glyphs or path
    triangulation for content that is still displayed; only placement and order change.
    """

    def __init__(self, content: Callable[[_Record], Hashable], native: Callable[[_Item], QQuickItem]) -> None:
        self._content: Callable[[_Record], Hashable] = content
        self._native: Callable[[_Item], QQuickItem] = native
        self._shown: list[tuple[_Item, _Record]] = []
        self._spare: list[_Item] = []

    @property
    def items(self) -> list[_Item]:
        """Return every retained item, displayed first."""
        return [item for item, _ in self._shown] + self._spare

    def assign(
        self, records: Sequence[_Record], create: Callable[[], _Item], spare: int
    ) -> list[tuple[_Item, _Record, bool]]:
        """Pair records with items, flagging pairs whose item already shows that exact record."""
        displayed: defaultdict[Hashable, deque[tuple[_Item, _Record]]] = defaultdict(deque)
        for item, shown in self._shown:
            displayed[self._content(shown)].append((item, shown))
        pairs: list[tuple[_Item, _Record, bool]] = []
        unmatched: list[_Record] = []
        for record in records:
            candidates = displayed.get(self._content(record))
            if candidates:
                item, shown = candidates.popleft()  # Equal content keeps its relative order.
                pairs.append((item, record, shown == record))
            else:
                unmatched.append(record)
        released = [item for candidates in displayed.values() for item, _ in candidates]
        for record in unmatched:
            item = released.pop() if released else self._spare.pop() if self._spare else create()
            self._native(item).setVisible(True)
            pairs.append((item, record, False))
        for item in released:
            self._native(item).setVisible(False)
        self._spare.extend(released)
        while len(self._spare) > spare:
            _retire(self._native(self._spare.pop()))
        self._shown = [(item, record) for item, record, _ in pairs]
        return pairs


class QuickOverlay:
    """Bind prepared buffers and layouts; no catalog or shape interpretation occurs here."""

    def __init__(self, parent: QQuickItem, components: ShapeComponents) -> None:
        self.item = QQuickItem(parent)
        self._components = components
        self._paths: _ContentSlots[_PathItem, PathData] = _ContentSlots(_path_content, lambda path: path.item)
        self._texts: _ContentSlots[TextItem, TextData] = _ContentSlots(_text_content, lambda text: text)
        self._labels = LabelLayer(self.item)
        self._labels.setZ(_LABEL_Z)
        self._geometry: list[GeometryItem] = []
        self._drawing = EMPTY_DRAWING

    def present(self, drawing: PreparedDrawing | None, opacity: float) -> None:
        """Update changed final output while preserving item identity and native transforms."""
        self.item.setOpacity(opacity)
        drawing = drawing if drawing is not None else EMPTY_DRAWING
        previous = self._drawing
        if drawing is previous:
            return
        for index, mesh in enumerate(drawing.meshes):
            if index < len(previous.meshes) and mesh == previous.meshes[index]:
                continue
            if index == len(self._geometry):
                self._geometry.append(GeometryItem(self.item))
                self._geometry[index].setZ(_MESH_Z)
            self._geometry[index].set_mesh(mesh.payload)
        spare = _SPARE if len(drawing) else 0
        for layer, path, unchanged in self._paths.assign(
            drawing.paths, lambda: _PathItem(self.item, self._components), spare
        ):
            if not unchanged:
                layer.update(path)
        for text_item, text, unchanged in self._texts.assign(drawing.texts, self._create_text, spare):
            if not unchanged:
                text_item.set_text(text.block, text.position, text.color)
        self._labels.set_labels(drawing.labels)
        for item in self._geometry[len(drawing.meshes) :]:
            item.setVisible(False)
        while len(self._geometry) > len(drawing.meshes) + spare:
            _retire(self._geometry.pop())
        self._drawing = drawing

    def _create_text(self) -> TextItem:
        item = TextItem(self.item)
        item.setZ(_TEXT_Z)
        return item
