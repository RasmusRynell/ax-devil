"""One row of a sidebar list: an icon, a name, muted detail and the actions that belong to it."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.tokens import Height, Space, TextRole

_ICON_PX = 16
_MUTED = "color: palette(placeholder-text);"


def muted_label(text: str, role: TextRole = TextRole.SMALL) -> QLabel:
    """Return a label in *role* with the muted text color."""
    label = QLabel(text)
    role.apply(label)
    label.setStyleSheet(_MUTED)
    return label


class ListRow(QFrame):
    """A row with a *title*, optionally an *icon*, a *subtitle* under the title or a *detail* at the right, and
    *trailing* widgets such as buttons.

    ``activated`` is emitted by a click on the row itself, never by its trailing widgets. An *active* row has an accent
    edge; a *dim* row's title is muted. *indent* moves the row in under the one above, and a *wrap*ped subtitle may
    take several lines where an unwrapped one is elided.
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
        column.setSpacing(0)
        name = ElidedLabel(title, Qt.TextElideMode.ElideRight, self)
        if dim:
            name.setStyleSheet(_MUTED)
        column.addWidget(name)
        if subtitle:
            line: QLabel
            if wrap:
                line = QLabel(subtitle, self)
                line.setWordWrap(True)
            else:
                line = ElidedLabel(subtitle, Qt.TextElideMode.ElideRight, self)
            TextRole.SMALL.apply(line)
            line.setStyleSheet(_MUTED)
            column.addWidget(line)
        layout.addLayout(column, 1)
        if detail:
            layout.addWidget(muted_label(detail))
        for widget in trailing:
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        edge = "palette(highlight)" if active else "transparent"
        self.setStyleSheet(
            f"""
            #AxDevilListRow {{ border-left: 2px solid {edge}; }}
            #AxDevilListRow:hover {{ background: palette(midlight); }}
            """
        )
        follow_appearance(self, lambda: self.setMinimumHeight(Height.ROW.px))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Report a left click on the row, wherever no trailing widget took it."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)
