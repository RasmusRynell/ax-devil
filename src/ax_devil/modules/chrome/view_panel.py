"""The frame of a sidebar view: a pane header with the view's title over a column of content."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.editable_label import EditableLabel
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Height, Space, TextRole


class ViewPanel(QWidget):
    """A pane header, with an optional widget at its right end, over the *body* layout views fill.

    Keep the body's rows at the top with ``add_stretch`` after the last one, so a short list does not spread out.
    With *editable_title*, clicking the title edits it in place and ``title_edited`` carries the entered text; the
    header shows it only once ``set_title`` does.
    """

    title_edited = Signal(str)

    def __init__(
        self,
        title: str,
        header_widget: QWidget | None = None,
        parent: QWidget | None = None,
        *,
        editable_title: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilViewPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget(self)
        self._header.setObjectName("AxDevilViewHeader")
        self._header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(Space.L, 0, Space.M, 0)
        header_layout.setSpacing(Space.M)
        self._title: ElidedLabel
        if editable_title:
            self._title = EditableLabel(title, Qt.TextElideMode.ElideRight, self._header)
            self._title.edited.connect(self.title_edited)
        else:
            self._title = ElidedLabel(title, Qt.TextElideMode.ElideRight, self._header)
        TextRole.STRONG.apply(self._title)
        header_layout.addWidget(self._title, 1)
        if header_widget is not None:
            header_layout.addWidget(header_widget)
        layout.addWidget(self._header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        layout.addLayout(self.body, 1)
        follow_appearance(self, self._apply_appearance)

    def title(self) -> str:
        """Return the title in the header, before eliding."""
        return self._title.full_text()

    def set_title(self, title: str, tooltip: str = "") -> None:
        """Show *title* in the header, elided to fit, with *tooltip* over it (the whole title when empty)."""
        self._title.set_full_text(title)
        if tooltip:
            self._title.setToolTip(tooltip)

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        """Append *widget* to the body, across its full width; with a *stretch*, it takes the room left over."""
        self.body.addWidget(widget, stretch)

    def add_padded(self, widget: QWidget, *, top: int = Space.M, bottom: int = Space.S) -> QWidget:
        """Append *widget* to the body with the side margins rows have, and *top* and *bottom* room.

        Return the padding around it, which is what to hide to hide the widget with its room. The padding and the plain
        containers in it let the panel's background through.
        """
        holder = QWidget(self)
        holder.setStyleSheet(SEE_THROUGH)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(Space.M, top, Space.M, bottom)
        layout.setSpacing(Space.M)
        layout.addWidget(widget, 1)
        self.body.addWidget(holder)
        return holder

    def add_stretch(self) -> None:
        """Take the remaining room, so what comes after sits at the bottom."""
        self.body.addStretch(1)

    def _apply_appearance(self) -> None:
        self._header.setFixedHeight(Height.PANE_HEADER.px)
        border = palette_color_css(self.palette(), QPalette.ColorRole.Mid, alpha=0.5)
        self.setStyleSheet(
            f"""
            #AxDevilViewPanel {{ background: palette(alternate-base); }}
            #AxDevilViewHeader {{ border-bottom: 1px solid {border}; background: transparent; }}
            """
        )
