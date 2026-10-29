"""A small rounded picture that follows the pointer to show what the row under it stands for."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath, QPaintEvent, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.tokens import Radius, Space

LONG_EDGE_PX = 180


class HoverCard(QWidget):
    """A tooltip-like window that is only a picture, never takes focus or clicks.

    The picture keeps its aspect ratio with its longer side ``LONG_EDGE_PX`` logical pixels, has rounded corners and a
    thin outline that reads on light and dark backgrounds. It is drawn on a translucent window.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a hidden card; *parent* only keeps it alive."""
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._image = QImage()
        self._pixmap = QPixmap()

    def set_picture(self, image: QImage) -> None:
        """Show *image*, resized to fit the card's long edge; a null image leaves the card as it was."""
        if image.isNull():
            return
        self._image = image
        self._pixmap = QPixmap()  # Scaled when painted, for the screen the card is on.
        size = image.size().scaled(QSize(LONG_EDGE_PX, LONG_EDGE_PX), Qt.AspectRatioMode.KeepAspectRatio)
        self.setFixedSize(size)
        self.update()

    def show_at(self, global_pos: QPoint) -> None:
        """Show the card down and right of *global_pos*, on the other side of it where it would leave the screen."""
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        left = global_pos.x() + Space.M
        top = global_pos.y() + Space.M
        if left + self.width() > area.right() + 1:
            left = global_pos.x() - Space.M - self.width()
        if top + self.height() > area.bottom() + 1:
            top = global_pos.y() - Space.M - self.height()
        self.move(max(area.left(), left), max(area.top(), top))
        self.show()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Paint the picture inside a rounded outline."""
        if self._image.isNull():
            return
        ratio = self.devicePixelRatioF()
        if self._pixmap.isNull() or self._pixmap.devicePixelRatio() != ratio:
            scaled = self._image.scaled(
                self.size() * ratio, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(ratio)
            self._pixmap = QPixmap.fromImage(scaled)
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = QPainterPath()
        shape.addRoundedRect(outline, Radius.POPUP, Radius.POPUP)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipPath(shape)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.setClipping(False)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.Mid), 1))
        painter.drawPath(shape)
        painter.end()
