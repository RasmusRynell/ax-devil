"""A strip that tells the user something needs attention, with the action that deals with it."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QLabel, QLayout, QLayoutItem, QWidget, QWidgetItem

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.palette_css import qcolor_to_css
from ax_devil.modules.chrome.small_button import ButtonKind, SmallButton
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole

_FILL_ALPHA = 0.10
_BORDER_ALPHA = 0.5
_UNBOUNDED = 16_777_215  # QWIDGETSIZE_MAX: wide enough that a label never wraps


class _BannerLayout(QLayout):
    """The message and its buttons on one row; when the message would not fit on one line there, the message takes
    the full width and the buttons go on a row below, right-aligned.

    The arrangement depends only on the width given, so it cannot flip back and forth while the banner is resized.
    """

    def __init__(self, parent: QWidget, message: QLabel, buttons: list[SmallButton]) -> None:
        super().__init__(parent)
        self._message = message
        self._buttons = buttons
        self._items: list[QLayoutItem] = []
        for widget in (message, *buttons):
            self.addChildWidget(widget)
            self._items.append(QWidgetItem(widget))
        self.setContentsMargins(Space.M, Space.S, Space.M, Space.S)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def _text_width(self) -> int:
        return self._message.fontMetrics().horizontalAdvance(self._message.text())

    def _buttons_width(self) -> int:
        return sum(button.sizeHint().width() for button in self._buttons) + Space.S * (len(self._buttons) - 1)

    def _button_height(self) -> int:
        return max(button.sizeHint().height() for button in self._buttons)

    def _one_row_width(self) -> int:
        return self._text_width() + Space.M + self._buttons_width()

    def _fits_one_row(self, inner_width: int) -> bool:
        """Whether the message stays on one line beside the buttons."""
        room = inner_width - Space.M - self._buttons_width()
        return room > 0 and self._message.heightForWidth(room) <= self._message.heightForWidth(_UNBOUNDED)

    def _margin_width(self) -> int:
        margins = self.contentsMargins()
        return margins.left() + margins.right()

    def _inner_height(self, inner_width: int) -> int:
        if self._fits_one_row(inner_width):
            message_width = inner_width - Space.M - self._buttons_width()
            return max(self._message.heightForWidth(message_width), self._button_height())
        return self._message.heightForWidth(inner_width) + Space.S + self._button_height()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self.contentsMargins()
        return self._inner_height(width - self._margin_width()) + margins.top() + margins.bottom()

    def sizeHint(self) -> QSize:
        width = self._one_row_width() + self._margin_width()
        return QSize(width, self.heightForWidth(width))

    def minimumSize(self) -> QSize:
        margins = self.contentsMargins()
        height = max(self._message.fontMetrics().height(), self._button_height()) + margins.top() + margins.bottom()
        return QSize(self._buttons_width() + self._margin_width(), height)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        area = self.contentsRect()
        buttons_width = self._buttons_width()
        one_row = self._fits_one_row(area.width())
        message_width = area.width() - Space.M - buttons_width if one_row else area.width()
        message_height = self._message.heightForWidth(message_width)
        row_height = self._button_height()
        if one_row:
            height = max(message_height, row_height)
            message_y = area.top() + (height - message_height) // 2
            buttons_y = area.top() + (height - row_height) // 2
        else:
            message_y = area.top()
            buttons_y = area.top() + message_height + Space.S
        self._message.setGeometry(area.left(), message_y, message_width, message_height)
        x = area.right() + 1 - buttons_width
        for button in self._buttons:
            size = button.sizeHint()
            button.setGeometry(x, buttons_y, size.width(), row_height)
            x += size.width() + Space.S


class Banner(QFrame):
    """A warning-colored message with an action button; connect to ``action`` to react to a click.

    Give *dismiss_text* for a button beside the action that only hides the banner.
    """

    def __init__(
        self,
        message: str,
        action_text: str,
        dismiss_text: str | None = None,
        action_kind: ButtonKind = ButtonKind.PRIMARY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilBanner")
        text = QLabel(message, self)
        TextRole.SMALL.apply(text)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        buttons: list[SmallButton] = []
        self.dismiss: SmallButton | None = None
        if dismiss_text is not None:
            self.dismiss = SmallButton(dismiss_text, kind=ButtonKind.OUTLINE, parent=self)
            self.dismiss.clicked.connect(self.hide)
            buttons.append(self.dismiss)
        self.action = SmallButton(action_text, kind=action_kind, parent=self)
        buttons.append(self.action)
        self.setLayout(_BannerLayout(self, text, buttons))
        follow_appearance(self, self._apply_appearance)

    def _apply_appearance(self) -> None:
        warning = StatusColor.WARNING.color(self.palette())
        fill = QColor(warning)
        fill.setAlphaF(_FILL_ALPHA)
        self.setStyleSheet(
            f"""
            #AxDevilBanner {{
                border: 1px solid {qcolor_to_css(warning, alpha=_BORDER_ALPHA)};
                border-radius: {Radius.CONTROL}px;
                background: {fill.name(QColor.NameFormat.HexArgb)};
            }}
            """
        )
