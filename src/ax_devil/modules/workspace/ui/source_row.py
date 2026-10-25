"""The look every sidebar view gives a browser row and a browser section, built from the chrome list kit."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.list_row import ListRow, muted_label
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.ui.browser_rows import (
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)


def _hidden_note(hidden: int) -> str:
    """Return what a caption adds for *hidden* rows the list leaves out, or nothing for none."""
    return f"{hidden} filtered out" if hidden else ""


def section_accessible_text(section: WorkspaceBrowserSection, hidden: int) -> str:
    """Return what a screen reader says for the caption ``section_caption`` draws: the same title, count and note."""
    counted = f"{section.kind.title}, {len(section.rows)}"
    note = _hidden_note(hidden)
    return f"{counted}, {note}" if note else counted


def section_caption(
    section: WorkspaceBrowserSection, text_indent: int, *, hidden: int = 0, folded: bool | None = None
) -> QWidget:
    """Return the caption that heads *section*: its title and row count, then how many of its rows the list leaves
    out (*hidden*), marked in its kind's color or muted, with a fold arrow when *folded* is given.

    A section without rows has no count, and says its kind's empty text under the caption, *text_indent* in.
    """
    kind = section.kind
    hidden_note = _hidden_note(hidden)
    mark = section.mark_color
    caption = SectionCaption(kind.title, len(section.rows) or None, mark.color, note=hidden_note, folded=folded)
    if section.rows or not kind.empty_text:
        return caption
    heading = QWidget()
    heading.setStyleSheet(SEE_THROUGH)
    layout = QVBoxLayout(heading)
    layout.setContentsMargins(0, 0, 0, Space.S)
    layout.setSpacing(0)
    layout.addWidget(caption)
    note = muted_label(kind.empty_text)
    note.setContentsMargins(text_indent, 0, 0, 0)
    layout.addWidget(note)
    return heading


def _action_button(action: RowAction) -> SmallButton:
    """Return the button that offers *action*."""
    button = SmallButton(action.button_text, action.icon)
    button.setFixedWidth(button.sizeHint().width())
    button.setToolTip(action.tooltip)
    return button


def row_look(row: WorkspaceBrowserRow) -> tuple[object, ...]:
    """Return the values ``SourceRow`` draws or binds for *row*; a widget can be kept while they stay equal."""
    return (
        row.label,
        row.detail,
        row.wrap_detail,
        row.mark,
        row.is_dimmed,
        row.is_open,
        row.inline_action,
        row.tooltip,
    )


class SourceRow(ListRow):
    """*row* as a list row: its kind's mark, its name over its detail, and its inline action.

    The mark is muted and the name dimmed as the row says; a row open in a viewer is the list row's active one.
    Hovering shows the full name over the row's tooltip. ``action_requested`` carries the row and the inline action
    pressed; a click on the row itself is ``activated``. Kind colors are drawn for the palette at construction, so
    owners build rows again when the theme changes.
    """

    action_requested = Signal(object, object)  # WorkspaceBrowserRow, RowAction

    row: WorkspaceBrowserRow
    """The row the widget shows; the tree sets it to the current row while its look is unchanged."""

    def __init__(self, row: WorkspaceBrowserRow, parent: QWidget | None = None) -> None:
        trailing: list[QWidget] = []
        if (inline := row.inline_action) is not None:
            action = inline.action
            button = _action_button(action)
            button.clicked.connect(lambda _checked=False: self.action_requested.emit(self.row, action))
            trailing.append(button)
        icon, color = row.mark
        super().__init__(
            row.label,
            icon=icon.icon(color.color(QGuiApplication.palette())),
            subtitle=row.detail,
            wrap=row.wrap_detail,
            trailing=trailing,
            active=row.is_open,
            dim=row.is_dimmed,
            parent=parent,
        )
        self.row = row
        lines = "".join(f"<br>{escape(line)}" for line in row.tooltip.splitlines())
        self.setToolTip(f"<b>{escape(row.label)}</b>{lines}")
        for label in self.findChildren(QLabel):
            label.setToolTip("")  # The name's and detail's own tooltips would hide the row's, which has the reason.
