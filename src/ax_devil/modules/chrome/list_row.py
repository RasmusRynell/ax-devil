"""One row of a sidebar list: an icon, a name, muted detail and the actions that belong to it, on one line."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent, QPainter, QPalette, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import Chip
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Height, Space, TextRole, keep_minimum_chars

_ICON_PX = 16
_EDGE_PX = 2
_MIN_TITLE_CHARS = 6
_MUTED = "color: palette(placeholder-text);"


def muted_label(text: str, role: TextRole = TextRole.SMALL) -> QLabel:
    """Return a label in *role* with the muted text color."""
    label = QLabel(text)
    role.apply(label)
    label.setStyleSheet(_MUTED)
    return label


def paint_row_background(painter: QPainter, rect: QRect, palette: QPalette, *, active: bool, hovered: bool) -> None:
    """Fill *rect* the way a ``ListRow`` looks, for rows that paint themselves.

    An active or hovered row gets the midlight background; an active row also gets the accent edge on its left.
    """
    if active or hovered:
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Midlight))
    if active:
        painter.fillRect(
            QRect(rect.left(), rect.top(), _EDGE_PX, rect.height()), palette.color(QPalette.ColorRole.Highlight)
        )


class _FitLabel(ElidedLabel):
    """An elided label that asks for its whole text but can shrink to nothing, so a row short of room elides it."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, Qt.TextElideMode.ElideRight, parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the width of the whole text, with the pixel a rounded-down advance would otherwise elide it for."""
        margins = self.contentsMargins()
        width = self.fontMetrics().horizontalAdvance(self.full_text()) + 1 + margins.left() + margins.right()
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return no width, so the label can elide down to nothing."""
        return QSize(0, super().minimumSizeHint().height())


class ListRow(QFrame):
    """A row with a *title*, optionally an *icon* and a *tag* before it, a muted *detail* after it, and *trailing*
    widgets such as buttons, all on one line.

    A *subtitle* and *lines* of muted text sit under the title line; a *subtitle_warning* draws the subtitle in the
    warning color. The detail elides before the title does, and the title keeps room for a few characters.
    ``activated`` is emitted by a click on the row itself, never by its trailing widgets. An *active* row has an accent
    edge over the hover background; a *dim* row's title is muted. *indent* moves the row in under the one above, and a
    *wrap*ped subtitle may take several lines where an unwrapped one is elided.
    """

    activated = Signal()

    def __init__(
        self,
        title: str,
        *,
        icon: QIcon | None = None,
        tag: str = "",
        subtitle: str = "",
        subtitle_warning: bool = False,
        lines: Sequence[str] = (),
        detail: str = "",
        trailing: Sequence[QWidget] = (),
        active: bool = False,
        dim: bool = False,
        indent: int = 0,
        wrap: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilListRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M + indent, Space.XS, Space.M, Space.XS)
        layout.setSpacing(Space.XS)
        head = QHBoxLayout()
        head.setSpacing(Space.M)
        layout.addLayout(head)
        self._body = layout
        self._head = head
        self._aside: QWidget | None = None
        centered = Qt.AlignmentFlag.AlignVCenter
        if icon is not None:
            mark = QLabel(self)
            mark.setPixmap(icon.pixmap(_ICON_PX, _ICON_PX))
            mark.setFixedWidth(_ICON_PX)
            head.addWidget(mark, 0, centered)
        if tag:
            head.addWidget(Chip(tag, parent=self), 0, centered)
        name = _FitLabel(title, self)
        keep_minimum_chars(name, _MIN_TITLE_CHARS)
        if dim:
            name.setStyleSheet(_MUTED)
        head.addWidget(name, 0, centered)
        head.addStretch(1)
        if detail:
            aside = _FitLabel(detail, self)
            self._aside = aside
            TextRole.SMALL.apply(aside)
            aside.setStyleSheet(_MUTED)
            aside.setAlignment(Qt.AlignmentFlag.AlignRight | centered)
            head.addWidget(aside, 0, centered)
        for widget in trailing:
            head.addWidget(widget, 0, centered)
        if subtitle or lines:
            below = QVBoxLayout()
            below.setContentsMargins(_ICON_PX + Space.M if icon is not None else 0, 0, 0, 0)
            below.setSpacing(Space.XS)
            layout.addLayout(below)
            if subtitle:
                below.addWidget(self._subtitle_label(subtitle, warning=subtitle_warning, wrap=wrap))
            for text in lines:
                below.addWidget(self._muted_line(text))
        edge = "palette(highlight)" if active else "transparent"
        background = "palette(midlight)" if active else "transparent"
        # The theme borders every QFrame, labels included; a bordered label measures a wrapped line narrower than it
        # draws it, so a wrapped subtitle would get room for a line it never uses.
        self.setStyleSheet(
            f"""
            #AxDevilListRow {{ border-left: {_EDGE_PX}px solid {edge}; background: {background}; }}
            #AxDevilListRow:hover {{ background: palette(midlight); }}
            #AxDevilListRow QLabel {{ border: none; }}
            """
        )
        follow_appearance(self, self.updateGeometry)

    def _muted_line(self, text: str) -> ElidedLabel:
        """Return an elided line of small muted text."""
        line = ElidedLabel(text, Qt.TextElideMode.ElideRight, self)
        TextRole.SMALL.apply(line)
        line.setStyleSheet(_MUTED)
        return line

    def _subtitle_label(self, text: str, *, warning: bool, wrap: bool) -> QLabel:
        """Return the subtitle line: elided, or wrapped over as many lines as it takes."""
        line: QLabel
        if wrap:
            line = QLabel(text, self)
            line.setTextFormat(Qt.TextFormat.PlainText)
            line.setWordWrap(True)
            line.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        else:
            line = ElidedLabel(text, Qt.TextElideMode.ElideRight, self)
        TextRole.SMALL.apply(line)
        line.setStyleSheet(_MUTED)
        if warning:
            follow_appearance(line, lambda: line.setStyleSheet(StatusColor.WARNING.css(line.palette())))
        return line

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the size the content needs, at least a row high."""
        return super().sizeHint().expandedTo(QSize(0, Height.ROW.px))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the smallest size that shows the content uncut, at least a row high.

        A hint rather than a minimum height, which would let a list short of room squeeze the text instead of scroll.
        """
        return super().minimumSizeHint().expandedTo(QSize(0, Height.ROW.px))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Give the detail only the room the rest of the line leaves, so it elides before the title does."""
        super().resizeEvent(event)
        if self._aside is None:
            return
        others = [self._head.itemAt(index) for index in range(self._head.count())]
        used = sum(item.sizeHint().width() for item in others if item is not None and item.widget() is not self._aside)
        margins = self._body.contentsMargins()
        sides = margins.left() + margins.right()
        free = self.contentsRect().width() - sides - used - self._head.spacing() * (self._head.count() - 1)
        self._aside.setMaximumWidth(max(0, free))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Report a left click on the row, wherever no trailing widget took it."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)
