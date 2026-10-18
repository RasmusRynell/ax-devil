"""Native scene graph items; all resource operations happen in updatePaintNode."""

from __future__ import annotations

from collections import OrderedDict
from typing import cast

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QRhi, QRhiResourceUpdateBatch, QRhiTexture
from PySide6.QtQuick import (
    QQuickItem,
    QQuickWindow,
    QSGImageNode,
    QSGNode,
    QSGTexture,
)
from shiboken6 import delete

_UPLOADS: OrderedDict[int, QImage] = OrderedDict()


def _upload_image(image: QImage) -> QImage:
    # Comparison lanes share source QImages. Convert once on the GUI thread (every surface renders there),
    # keeping only two recent pixel buffers, never a video history.
    images, key = _UPLOADS, image.cacheKey()
    converted = images.get(key)
    if converted is None:
        image_format = (
            QImage.Format.Format_RGBA8888_Premultiplied if image.hasAlphaChannel() else QImage.Format.Format_RGBX8888
        )
        converted = image.convertToFormat(image_format)
        images[key] = converted
        while len(images) > 2:
            images.popitem(last=False)
    else:
        images.move_to_end(key)
    return converted


class _VideoTexture(QSGTexture):
    """Reuse Qt-owned RHI storage and enqueue only the newest image's pixels."""

    def __init__(self, window: QQuickWindow, image: QImage) -> None:
        super().__init__()
        self._alpha = image.hasAlphaChannel()
        self._base = window.createTextureFromImage(_upload_image(image))
        self._base.setParent(self)
        self._pending = QImage()

    def set_image(self, image: QImage) -> None:
        """Retain the next upload until Qt submits its resource update batch."""
        self._pending = QImage(image)

    def comparisonKey(self) -> int:  # noqa: N802
        """Keep native material identity stable across frame uploads."""
        return self._base.comparisonKey()

    def textureSize(self) -> QSize:  # noqa: N802
        """Return the size of the allocated storage."""
        return self._base.textureSize()

    def hasAlphaChannel(self) -> bool:  # noqa: N802
        """Use the same alpha semantics for every uploaded frame."""
        return self._alpha

    def hasMipmaps(self) -> bool:  # noqa: N802
        """Video uploads have one mip level."""
        return False

    def rhiTexture(self) -> QRhiTexture:  # noqa: N802
        """Expose the native resource allocated and owned by Qt."""
        return self._base.rhiTexture()

    def commitTextureOperations(self, rhi: QRhi, resource_updates: QRhiResourceUpdateBatch) -> None:  # noqa: N802
        """Upload new pixels without replacing the image node or texture storage."""
        self._base.commitTextureOperations(rhi, resource_updates)
        if not self._pending.isNull():
            resource_updates.uploadTexture(self._base.rhiTexture(), _upload_image(self._pending))
            self._pending = QImage()


class VideoTextureItem(QQuickItem):
    """Display the latest CPU image as a retained scene graph texture."""

    def __init__(self, parent: QQuickItem) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents)
        self._image = QImage()
        self._uploaded_key: int | None = None
        self._texture: _VideoTexture | None = None

    def set_image(self, image: QImage) -> None:
        """Retain an implicitly shared image until the scene graph consumes it."""
        if self._image.cacheKey() != image.cacheKey():
            self._image = QImage(image)
            self.update()

    def updatePaintNode(  # type: ignore[override]  # Qt permits null nodes; PySide stubs do not.  # noqa: N802
        self,
        old_node: QSGNode | None,
        data: QQuickItem.UpdatePaintNodeData,
    ) -> QSGNode | None:
        """Create/update texture resources only while Qt owns the render phase."""
        window = self.window()
        if window is None or self._image.isNull():
            if old_node is not None:
                delete(old_node)
            self._uploaded_key = None
            self._texture = None
            return None
        key = self._image.cacheKey()
        if (
            old_node is not None
            and self._uploaded_key != key
            and (
                self._texture is None
                or self._texture.textureSize() != self._image.size()
                or self._texture.hasAlphaChannel() != self._image.hasAlphaChannel()
            )
        ):
            # Retire the node with its owned texture as one unit. This also avoids
            # stale Shiboken parent references when replacing an owned texture.
            delete(old_node)
            old_node = None
        node = cast(QSGImageNode, old_node) if old_node is not None else window.createImageNode()
        if node is None:
            return None
        if old_node is None:
            self._texture = _VideoTexture(window, self._image)
            node.setTexture(self._texture)
            node.setOwnsTexture(True)
            self._uploaded_key = key
        elif self._uploaded_key != key and self._texture is not None:
            self._texture.set_image(self._image)
            node.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
            self._uploaded_key = key
        node.setFiltering(QSGTexture.Filtering.Nearest)
        node.setRect(self.boundingRect())
        return node
