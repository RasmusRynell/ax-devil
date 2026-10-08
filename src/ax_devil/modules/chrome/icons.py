"""Lucide icons packaged with the application, drawn in the current palette's colors."""

from __future__ import annotations

from enum import Enum
from functools import cache
from importlib.resources import files

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPalette, QPixmap, QPixmapCache
from PySide6.QtSvg import QSvgRenderer

RESOURCE_PACKAGE = "ax_devil.resources"
_DISABLED_OPACITY = 0.4


class Icon(Enum):
    """An icon from the bundled Lucide set: its SVG file and whether its shape is filled."""

    PLAY = ("play", True)
    PAUSE = ("pause", True)
    STEP_BACK = ("chevron-left", False)
    STEP_BACK_MANY = ("chevrons-left", False)
    STEP_FORWARD = ("chevron-right", False)
    STEP_FORWARD_MANY = ("chevrons-right", False)
    MINIMIZE = ("minus", False)
    MAXIMIZE = ("square", False)
    RESTORE = ("copy", False)
    CLOSE = ("x", False)
    EXPORT = ("download", False)
    MENU = ("chevron-down", False)
    MORE = ("ellipsis", False)
    DRAG = ("grip-vertical", False)
    PIN = ("pin", False)
    PINNED = ("pin", True)
    SHOWN = ("eye", False)
    HIDDEN = ("eye-off", False)
    FILTER = ("funnel", False)
    FILTER_OFF = ("funnel-x", False)
    VIDEO = ("square-play", False)
    LIVE_VIDEO = ("video", False)
    PLAYLIST = ("list-video", False)
    OVERLAY = ("layers", False)
    WARNING = ("triangle-alert", False)
    BROWSE = ("folder", False)

    def __init__(self, file_stem: str, filled: bool) -> None:
        self.file_stem = file_stem
        self.filled = filled

    def icon(self, color: QColor | None = None) -> QIcon:
        """Return the icon in ``color``, or in the application palette's text color whenever it is painted.

        Without a color the icon follows theme changes on its own: widgets repaint on palette changes and
        each paint reads the current palette.
        """
        return QIcon(_IconEngine(self, color))


@cache
def _svg(icon: Icon) -> str:
    text = files(RESOURCE_PACKAGE).joinpath("icons").joinpath(f"{icon.file_stem}.svg").read_text(encoding="utf-8")
    return text.replace('fill="none"', 'fill="currentColor"') if icon.filled else text


def _render(icon: Icon, color: QColor, size: QSize, scale: float) -> QPixmap:
    """Render ``icon`` centered in ``size`` logical pixels, cached by icon, color, size and scale."""
    key = f"ax-devil-icon:{icon.name}:{color.rgba():08x}:{size.width()}x{size.height()}@{scale:g}"
    pixmap = QPixmap()
    if QPixmapCache.find(key, pixmap):
        return pixmap
    pixmap = QPixmap(size * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    side = min(size.width(), size.height())
    target = QRectF((size.width() - side) / 2, (size.height() - side) / 2, side, side)
    renderer = QSvgRenderer(QByteArray(_svg(icon).replace("currentColor", color.name()).encode()))
    painter = QPainter(pixmap)
    painter.setOpacity(color.alphaF())
    renderer.render(painter, target)
    painter.end()
    QPixmapCache.insert(key, pixmap)
    return pixmap


class _IconEngine(QIconEngine):
    """Draw one bundled SVG icon at any size in a fixed color or the current palette's."""

    def __init__(self, icon: Icon, color: QColor | None) -> None:
        super().__init__()
        self._icon = icon
        self._color = color

    def clone(self) -> QIconEngine:
        """Return a copy that draws the same icon in the same color."""
        return _IconEngine(self._icon, self._color)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        """Return the icon at ``size`` device-independent pixels."""
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:  # noqa: N802
        """Return the icon at ``size`` logical pixels for a ``scale`` device pixel ratio."""
        return _render(self._icon, self._color_for(mode), size, scale)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        """Draw the icon into ``rect``."""
        scale = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, scale))

    def _color_for(self, mode: QIcon.Mode) -> QColor:
        if self._color is not None:
            color = QColor(self._color)
            if mode == QIcon.Mode.Disabled:
                color.setAlphaF(color.alphaF() * _DISABLED_OPACITY)
            return color
        palette = QGuiApplication.palette()
        if mode == QIcon.Mode.Disabled:
            return palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        if mode == QIcon.Mode.Selected:
            return palette.color(QPalette.ColorRole.HighlightedText)
        return palette.color(QPalette.ColorRole.WindowText)
