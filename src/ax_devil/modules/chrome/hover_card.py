"""A small card that appears beside a list row to show what the row stands for: its name, facts and a picture."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import VIDEO_CANVAS
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole

_WIDTH_IN_BODY_PX = 18
_STYLE = f"""
QFrame#HoverCard {{
    background-color: palette(base);
    border: 1px solid palette(mid);
    border-radius: {Radius.POPUP}px;
}}
QFrame#HoverCard QLabel {{ border: none; background: transparent; }}
"""
_LOADING_STYLE = f"background-color: {VIDEO_CANVAS.name()}; border-radius: {Radius.CONTROL}px;"


class HoverCard(QFrame):
    """A tooltip-like window with a title, lines of text and an optional picture, that never takes focus or clicks.

    The picture is a box as wide as ``18`` body-text pixels: a 16:9 dark box while it loads, then the image fitted
    inside the box's width and three quarters of that in height. With no picture the box is not there.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a hidden card; *parent* only keeps it alive and in the application's style."""
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(_STYLE)
        self._image: QImage | None = None
        self._loading = False
        self._anchor = QRect()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(Space.S)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._layout = layout
        self._title = self._label()
        TextRole.STRONG.apply(self._title)
        self._picture = QLabel(self)
        self._picture.hide()
        layout.addWidget(self._picture, 0, Qt.AlignmentFlag.AlignLeft)
        self._text = self._label()
        self._text.hide()
        follow_appearance(self, self._show_picture)

    def set_content(self, title: str, text: str) -> None:
        """Show *title* over the lines of *text*; empty text shows no lines."""
        if self._title.text() != title:
            self._title.setText(title)
        if self._text.text() != text:
            self._text.setText(text)
        self._text.setVisible(bool(text))
        self._place()

    def set_picture(self, image: QImage | None) -> None:
        """Show *image* fitted into the picture box, or no picture box when there is none or it is null."""
        self._image = image if image is not None and not image.isNull() else None
        self._loading = False
        self._show_picture()

    def set_picture_pending(self) -> None:
        """Show the dark loading box where the picture will be."""
        self._image = None
        self._loading = True
        self._show_picture()

    def show_beside(self, anchor: QRect) -> None:
        """Show the card with its top at the top of *anchor*, a global rectangle, and its left just right of it.

        The card stays inside the screen of *anchor*, even where that covers the anchor.
        """
        self._anchor = anchor
        self._place()
        self.show()

    def _label(self) -> QLabel:
        """Return a left-aligned plain-text label, added to the layout, whose text is never wrapped."""
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._layout.addWidget(label)
        return label

    def _show_picture(self) -> None:
        """Draw the picture box for the current state, at the current text size and screen scale."""
        width = _WIDTH_IN_BODY_PX * TextRole.BODY.px
        picture = self._picture
        if self._image is not None:
            ratio = self.devicePixelRatioF()
            scaled = self._image.scaled(
                QSize(width, width * 3 // 4) * ratio,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            picture.setStyleSheet("")
            picture.setPixmap(QPixmap.fromImage(scaled))
            picture.setFixedSize(QSize(round(scaled.width() / ratio), round(scaled.height() / ratio)))
        elif self._loading:
            picture.clear()
            picture.setStyleSheet(_LOADING_STYLE)
            picture.setFixedSize(width, width * 9 // 16)
        else:
            picture.clear()
        picture.setVisible(self._image is not None or self._loading)
        self._place()

    def _place(self) -> None:
        """Fit the card to its content and, once shown, keep it beside its anchor and inside the screen."""
        self._layout.activate()
        if self._anchor.isNull():
            return
        screen = QGuiApplication.screenAt(self._anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size = self.sizeHint()
        left = min(self._anchor.right() + Space.S, area.right() + 1 - size.width())
        top = min(self._anchor.top(), area.bottom() + 1 - size.height())
        self.move(max(area.left(), left), max(area.top(), top))
