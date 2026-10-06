"""Monochrome icons packaged with the application and tinted to match the current palette."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

RESOURCE_PACKAGE = "ax_devil.resources"


def load_resource_icon(filename: str, color: QColor | None = None) -> QIcon:
    """Load an icon from packaged application resources, optionally tinting it."""
    icon_path = files(RESOURCE_PACKAGE).joinpath(filename)
    if color is None:
        return QIcon(str(icon_path))

    image = QImage(str(icon_path))
    if image.isNull():
        return QIcon(str(icon_path))

    tinted = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    tinted.fill(Qt.GlobalColor.transparent)

    painter = QPainter(tinted)
    painter.drawImage(0, 0, image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()

    return QIcon(QPixmap.fromImage(tinted))
