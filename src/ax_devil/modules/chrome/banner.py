"""A strip that tells the user something needs attention, with the action that deals with it."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole

_FILL_ALPHA = 0.12


class Banner(QFrame):
    """A warning-colored message with an action button; connect to ``action`` to react to a click.

    Give *dismiss_text* for a button beside the action that only hides the banner.
    """

    def __init__(
        self, message: str, action_text: str, dismiss_text: str | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        layout.setSpacing(Space.M)
        self._icon = QLabel(self)
        layout.addWidget(self._icon)
        text = QLabel(message, self)
        TextRole.SMALL.apply(text)
        text.setWordWrap(True)
        layout.addWidget(text, 1)
        self.dismiss: SmallButton | None = None
        if dismiss_text is not None:
            self.dismiss = SmallButton(dismiss_text, parent=self)
            self.dismiss.clicked.connect(self.hide)
            layout.addWidget(self.dismiss)
        self.action = SmallButton(action_text, parent=self)
        layout.addWidget(self.action)
        follow_appearance(self, self._apply_appearance)

    def _apply_appearance(self) -> None:
        warning = StatusColor.WARNING.color(self.palette())
        fill = QColor(warning)
        fill.setAlphaF(_FILL_ALPHA)
        self.setStyleSheet(
            f"""
            #AxDevilBanner {{
                border: 1px solid {warning.name()};
                border-radius: {Radius.CONTROL}px;
                background: {fill.name(QColor.NameFormat.HexArgb)};
            }}
            """
        )
        self._icon.setPixmap(Icon.WARNING.icon(warning).pixmap(16, 16))
