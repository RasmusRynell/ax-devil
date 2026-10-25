"""One row of a sidebar list: an icon, a name, muted detail and the actions that belong to it."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.tokens import Height, Space, TextRole

_ICON_PX = 16
_EDGE_PX = 2
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


class ListRow(QFrame):
    """A row with a *title*, optionally an *icon*, a *subtitle* under the title or a *detail* at the right, and
    *trailing* widgets such as buttons.

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
        subtitle: str = "",
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.M + indent, Space.XS, Space.M, Space.XS)
        layout.setSpacing(Space.M)
        if icon is not None:
            mark = QLabel(self)
            mark.setPixmap(icon.pixmap(_ICON_PX, _ICON_PX))
            mark.setFixedWidth(_ICON_PX)
            top = Qt.AlignmentFlag.AlignTop if subtitle else Qt.AlignmentFlag.AlignVCenter
            layout.addWidget(mark, 0, top)
        column = QVBoxLayout()
        column.setSpacing(Space.XS)
        name = ElidedLabel(title, Qt.TextElideMode.ElideRight, self)
        if dim:
            name.setStyleSheet(_MUTED)
        column.addWidget(name)
        if subtitle:
            line: QLabel
            if wrap:
                line = QLabel(subtitle, self)
                line.setTextFormat(Qt.TextFormat.PlainText)
                line.setWordWrap(True)
                line.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            else:
                line = ElidedLabel(subtitle, Qt.TextElideMode.ElideRight, self)
            TextRole.SMALL.apply(line)
            line.setStyleSheet(_MUTED)
            column.addWidget(line)
        layout.addLayout(column, 1)
        self._column = column
        if detail:
            layout.addWidget(muted_label(detail))
        for widget in trailing:
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
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

    def height_for_width(self, width: int) -> int:
        """Return the height the row needs when it is *width* wide, a wrapped subtitle in as many lines as it takes.

        The row's own layout answers this too wide: it shares the width out by the labels' preferred widths.
        """
        layout = self.layout()
        assert layout is not None
        frame = self.contentsMargins()  # The edge and the theme's border, which the layout sits inside.
        margins = layout.contentsMargins()
        others = sum(
            item.sizeHint().width() + layout.spacing()
            for item in map(layout.itemAt, range(layout.count()))
            if item is not None and item.layout() is not self._column
        )
        room = width - frame.left() - frame.right() - margins.left() - margins.right() - others
        return self._column.heightForWidth(room) + frame.top() + frame.bottom() + margins.top() + margins.bottom()

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the size the content needs, at least a row high."""
        return super().sizeHint().expandedTo(QSize(0, Height.ROW.px))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the smallest size that shows the content uncut, at least a row high.

        A hint rather than a minimum height, which would let a list short of room squeeze the text instead of scroll.
        """
        return super().minimumSizeHint().expandedTo(QSize(0, Height.ROW.px))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Report a left click on the row, wherever no trailing widget took it."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)
