"""One scene-graph item that draws a prepared overlay (shapes and atlas glyphs) with a single custom shader.

Everything Qt-owned (geometry, material, shader, page texture) is created in ``updatePaintNode`` and owned by the
node tree; the module globals below live for the whole process so that Qt never outlives the Python objects it
points at. A ``QSGTextureMaterial`` subclass supplies the sampler binding, which PySide cannot override in a shader.
"""

from __future__ import annotations

import ctypes
from functools import lru_cache
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtQuick import (
    QQuickItem,
    QQuickWindow,
    QSGGeometry,
    QSGGeometryNode,
    QSGMaterial,
    QSGMaterialShader,
    QSGMaterialType,
    QSGNode,
    QSGRendererInterface,
    QSGTexture,
    QSGTextureMaterial,
)
from shiboken6 import delete, wrapInstance

from .overlay_vertex import OVERLAY_VERTEX
from .preparation import EMPTY_DRAWING, PreparedDrawing

_SHADERS = Path(__file__).parent / "shaders"
_GL_FLOAT = 0x1406
_GL_UNSIGNED_BYTE = 0x1401
# (location, components, GL type) in OVERLAY_VERTEX order: position, uv, size, radius/stroke, dash/kind, fill, pen.
_ATTRIBUTE_SPECS = ((0, 2, _GL_FLOAT), (1, 2, _GL_FLOAT), (2, 2, _GL_FLOAT), (3, 2, _GL_FLOAT), (4, 2, _GL_FLOAT)) + (
    (5, 4, _GL_UNSIGNED_BYTE),
    (6, 4, _GL_UNSIGNED_BYTE),
)

# Process-lifetime globals (ownership rule): Qt keeps raw pointers to all four.
_ATTRIBUTE_ARRAY = (ctypes.c_int * (4 * len(_ATTRIBUTE_SPECS)))()
for _slot, (_location, _tuple, _gl_type) in enumerate(_ATTRIBUTE_SPECS):
    _ATTRIBUTE_ARRAY[4 * _slot : 4 * _slot + 4] = [_location, _tuple, _gl_type, 1 if _location == 0 else 0]
_ATTRIBUTES = cast(QSGGeometry.Attribute, wrapInstance(ctypes.addressof(_ATTRIBUTE_ARRAY), QSGGeometry.Attribute))
_ATTRIBUTE_SET = QSGGeometry.AttributeSet()
_ATTRIBUTE_SET.count = len(_ATTRIBUTE_SPECS)
_ATTRIBUTE_SET.stride = OVERLAY_VERTEX.itemsize
_ATTRIBUTE_SET.attributes = _ATTRIBUTES
_MATERIAL_TYPE = QSGMaterialType()


class _OverlayMaterial(QSGTextureMaterial):
    """Texture material whose shader pair is replaced by the overlay shaders."""

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QSGMaterial.Flag.Blending, True)
        self.setFiltering(QSGTexture.Filtering.Linear)

    def type(self) -> QSGMaterialType:
        """Return the shared type so Qt caches one pipeline for all overlay layers."""
        return _MATERIAL_TYPE

    def createShader(self, render_mode: QSGRendererInterface.RenderMode) -> QSGMaterialShader:
        """Return a new stock texture shader (Qt owns it, no Python reference is kept) pointed at our code."""
        shader = super().createShader(render_mode)
        shader.setShaderFileName(QSGMaterialShader.Stage.VertexStage, str(_SHADERS / "overlay.vert.qsb"))
        shader.setShaderFileName(QSGMaterialShader.Stage.FragmentStage, str(_SHADERS / "overlay.frag.qsb"))
        return shader


@lru_cache(maxsize=4)
def _quad_indices(quads: int) -> NDArray[np.uint32]:
    corners = np.array([0, 1, 2, 2, 1, 3], np.uint32)
    return (np.arange(quads, dtype=np.uint32)[:, None] * 4 + corners).reshape(-1)


def _page_key(page: QImage | None) -> int:
    return 0 if page is None else page.cacheKey()


class OverlayItem(QQuickItem):
    """Draws one prepared overlay: ``present`` hands it over, the next scene-graph sync uploads it."""

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._drawing = EMPTY_DRAWING
        self._changed = False
        self._uploaded_page = -1

    def present(self, drawing: PreparedDrawing, opacity: float) -> None:
        """Show *drawing* at *opacity*; the same drawing as before uploads nothing."""
        self.setOpacity(opacity)
        if drawing is self._drawing:
            return
        self._drawing = drawing
        self._changed = True
        self.update()

    def updatePaintNode(  # type: ignore[override]  # Qt permits null nodes; PySide stubs do not.  # noqa: N802
        self, old: QSGNode | None, data: QQuickItem.UpdatePaintNodeData
    ) -> QSGNode | None:
        """Build or refresh the node tree on the render thread."""
        window = self.window()
        if window is None or not len(self._drawing.vertices):
            if old is not None:
                delete(old)
            self._changed = True
            self._uploaded_page = -1
            return None
        return self._update_node(window, old)

    def _update_node(self, window: QQuickWindow, old: QSGNode | None) -> QSGNode:
        node = cast(QSGGeometryNode, old)
        if old is None:
            node = QSGGeometryNode()
            node.setMaterial(_OverlayMaterial())
            node.setFlag(QSGNode.Flag.OwnsMaterial, True)
            node.setGeometry(QSGGeometry(_ATTRIBUTE_SET, 0, 0, QSGGeometry.Type.UnsignedIntType.value))
            node.setFlag(QSGNode.Flag.OwnsGeometry, True)
            node.geometry().setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
            self._uploaded_page = -1
            self._changed = True
        dirty = QSGNode.DirtyStateBit(0)
        if self._changed:
            vertices = self._drawing.vertices
            quads = len(vertices) // 4
            geometry = node.geometry()
            geometry.allocate(quads * 4, quads * 6)
            ctypes.memmove(int(geometry.vertexData()), vertices.ctypes.data, vertices.nbytes)
            indices = _quad_indices(quads)
            ctypes.memmove(int(geometry.indexData()), indices.ctypes.data, indices.nbytes)
            dirty |= QSGNode.DirtyStateBit.DirtyGeometry
            self._changed = False
        page = self._drawing.page
        if _page_key(page) != self._uploaded_page:
            self._replace_page(window, node, page)
            dirty |= QSGNode.DirtyStateBit.DirtyMaterial
        node.markDirty(dirty)
        return node

    def _replace_page(self, window: QQuickWindow, node: QSGGeometryNode, page: QImage | None) -> None:
        """Park the page texture on a hidden child image node; the material borrows it."""
        while node.firstChild() is not None:
            child = node.firstChild()
            node.removeChildNode(child)
            delete(child)
        image = page if page is not None else QImage(1, 1, QImage.Format.Format_Alpha8)
        holder = window.createImageNode()
        holder.setTexture(window.createTextureFromImage(image))
        holder.setOwnsTexture(True)
        holder.setRect(QRectF())
        node.appendChildNode(holder)
        cast(_OverlayMaterial, node.material()).setTexture(holder.texture())
        self._uploaded_page = _page_key(page)
