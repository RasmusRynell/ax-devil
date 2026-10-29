"""Thumbnail size, shared by every source."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

THUMBNAIL_EDGE_PX = 480
"""The longest side of a thumbnail."""


def fit(image: QImage, edge: int) -> QImage:
    """Return *image* with its longest side at most *edge*, keeping the aspect ratio.

    Large images are first halved cheaply, then scaled smoothly, so a 4K frame costs little and still looks clean.
    """
    if max(image.width(), image.height()) <= edge:
        return image
    if max(image.width(), image.height()) > 2 * edge:
        image = image.scaled(
            2 * edge, 2 * edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
        )
    return image.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
