"""Single-line search box for the media tools' lists, with the query syntax one click away."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QInputMethodEvent, QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.popover import Popover
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.scene.query import Query, QueryToken, TokenKind, compile_query, tokenize

_DEBOUNCE_MS = 150
_TERM_TINT = 0.4
_FREE_TEXT_TINT = 0.22
_PLACEHOLDER = "Search objects"
# (example, meaning): one uniform list, no field names, because any path of an object's data can be searched.
_SYNTAX = (
    ("human 17", "text in id, class or state"),
    ("class:human", "field equals value"),
    ("score>0.7", "compare with > < >= <= ="),
    ("-state:parked", "exclude matches"),
    ("a OR b", "either term"),
    ("a b", "both terms (default)"),
)
_MUTED = "color: palette(placeholder-text);"


class QueryBar(QWidget):
    """Search field that turns its text into a compiled ``Query`` shortly after typing stops.

    Terms are tinted as they are typed; the "?" button opens a short syntax sheet of (example, meaning) rows, the
    objects' by default. A muted note beneath the field can say why a query finds nothing.
    """

    queryChanged = Signal(Query)  # noqa: N815

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        placeholder: str = _PLACEHOLDER,
        syntax: Sequence[tuple[str, str]] = _SYNTAX,
    ) -> None:
        """Create an empty bar with the *placeholder* text and the *syntax* rows of its sheet."""
        super().__init__(parent)
        self._emitted = ""
        self._syntax_rows = syntax
        self._syntax: _SyntaxPopup | None = None

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(placeholder)
        TextRole.MONO.apply(self._edit)
        self._clear = QAction(self._edit)
        self._clear.setToolTip("Clear")
        self._clear.setVisible(False)
        self._edit.addAction(self._clear, QLineEdit.ActionPosition.TrailingPosition)

        self._help = QToolButton(self)
        self._help.setAutoRaise(True)
        self._help.setToolTip("Query syntax")
        self._help.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # One line that never widens the page: a long field path is cut in the middle, whole in the tooltip.
        self._note = ElidedLabel("", Qt.TextElideMode.ElideMiddle, self)
        self._note.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._note)
        self._note.hide()

        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(Space.S)
        fields.addWidget(self._edit, 1)
        fields.addWidget(self._help)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(Space.XS)
        column.addLayout(fields)
        column.addWidget(self._note)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._emit)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._emit)
        self._clear.triggered.connect(self._clear_text)
        self._help.clicked.connect(self._show_syntax)
        follow_appearance(self, self._apply_appearance)

    def text(self) -> str:
        """Return the query text as typed."""
        return self._edit.text()

    def set_text(self, text: str) -> None:
        """Replace the query text; ``queryChanged`` follows after the usual delay if the query text changed."""
        self._edit.setText(text)

    def query(self) -> Query:
        """Return the current text compiled, without waiting for the delay."""
        return compile_query(self._edit.text())

    def set_note(self, note: str) -> None:
        """Show ``note`` as a muted line beneath the field; an empty note hides the line."""
        self._note.set_full_text(note)
        self._note.setVisible(bool(note))

    def _apply_appearance(self) -> None:
        self._help.setIcon(Icon.HELP.icon())
        self._help.setFixedSize(Height.CONTROL.px, Height.CONTROL.px)
        self._clear.setIcon(Icon.CLOSE.icon())
        self._highlight()

    def _on_text_changed(self, text: str) -> None:
        self._clear.setVisible(bool(text))
        self._highlight()
        self._timer.start()

    def _clear_text(self) -> None:
        self._edit.clear()
        self._emit()
        self._edit.setFocus()

    def _emit(self) -> None:
        self._timer.stop()
        text = self._edit.text()
        if text != self._emitted:
            self._emitted = text
            self.queryChanged.emit(compile_query(text))

    def _highlight(self) -> None:
        """Tint each term of the text through the line edit's input-method formats, which style existing text."""
        palette = self._edit.palette()
        attributes = [
            QInputMethodEvent.Attribute(
                QInputMethodEvent.AttributeType.TextFormat,
                token.start - self._edit.cursorPosition(),
                token.end - token.start,
                _token_format(token, palette),
            )
            for token in tokenize(self._edit.text())
        ]
        attributes.append(QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.Cursor, 0, 1, QColor()))
        QApplication.sendEvent(self._edit, QInputMethodEvent("", attributes))

    def _show_syntax(self) -> None:
        if self._syntax is None:
            self._syntax = _SyntaxPopup(self, self._syntax_rows)
        self._syntax.open_below(self._help)


def _token_format(token: QueryToken, palette: QPalette) -> QTextCharFormat:
    """Return how a span of the query is drawn: terms on a tint of the selection color, the rest muted."""
    fmt = QTextCharFormat()
    muted = palette.color(QPalette.ColorRole.PlaceholderText)
    if token.kind in (TokenKind.OPERATOR, TokenKind.OR):
        fmt.setForeground(muted)
    elif token.kind is TokenKind.INVALID:
        fmt.setForeground(muted)
        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
        fmt.setUnderlineColor(muted)
    if token.kind not in (TokenKind.OR, TokenKind.INVALID):
        tint = QColor(palette.color(QPalette.ColorRole.Highlight))
        tint.setAlphaF(_FREE_TEXT_TINT if token.kind is TokenKind.TEXT else _TERM_TINT)
        fmt.setBackground(tint)
    return fmt


class _SyntaxPopup(Popover):
    """The static syntax sheet: what to type and what it does."""

    def __init__(self, owner: QWidget, rows: Sequence[tuple[str, str]]) -> None:
        super().__init__(owner)
        grid = QGridLayout(self)
        grid.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        grid.setHorizontalSpacing(Space.L)
        grid.setVerticalSpacing(Space.S)
        for index, (example, meaning) in enumerate(rows):
            code = QLabel(example, self)
            TextRole.MONO.apply(code)
            text = QLabel(meaning, self)
            text.setStyleSheet(_MUTED)
            TextRole.SMALL.apply(text)
            grid.addWidget(code, index, 0)
            grid.addWidget(text, index, 1)
