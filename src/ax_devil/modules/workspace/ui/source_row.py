"""The look every sidebar view gives a browser row, built from the chrome list kit."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow


def _action_button(action: RowAction) -> SmallButton:
    """Return the button that offers *action*; one without text is named by its hover text."""
    button = SmallButton(action.button_text, action.icon)
    button.setFixedWidth(button.sizeHint().width())
    name = action.tooltip or action.menu_text
    button.setToolTip(name)
    button.setAccessibleName(name)
    return button


def row_accessible_text(row: WorkspaceBrowserRow) -> str:
    """Return what a screen reader says for *row*: its label, then its detail line."""
    return f"{row.label}, {row.detail}" if row.detail else row.label


def row_look(row: WorkspaceBrowserRow, *, info_shown: bool = False) -> tuple[object, ...]:
    """Return the values ``SourceRow`` draws or binds for *row*; a widget can be kept while they stay equal."""
    return (
        row.label,
        row.tag,
        row.aside,
        row.detail,
        row.detail_is_warning,
        row.info if info_shown else (),
        row.mark,
        row.is_dimmed,
        row.is_open,
        row.inline_actions,
        row.tooltip,
    )


class SourceRow(ListRow):
    """*row* as a one-line list row: its kind's mark, tag, name, aside and inline action buttons, with its detail
    and, while *info_shown*, its info lines under it.

    The mark is muted and the name dimmed as the row says; a row open in a viewer is the list row's active one.
    Hovering shows the full name over the row's tooltip. ``action_requested`` carries the row and the inline action
    pressed; a click on the row itself is ``activated``. Kind colors are drawn for the palette at construction, so
    owners build rows again when the theme changes.
    """

    action_requested = Signal(object, object)  # WorkspaceBrowserRow, RowAction

    row: WorkspaceBrowserRow
    """The row the widget shows; the tree sets it to the current row while its look is unchanged."""

    def __init__(self, row: WorkspaceBrowserRow, *, info_shown: bool = False, parent: QWidget | None = None) -> None:
        trailing: list[QWidget] = []
        for inline in row.inline_actions:
            button = _action_button(inline.action)
            button.clicked.connect(
                lambda _checked=False, action=inline.action: self.action_requested.emit(self.row, action)
            )
            trailing.append(button)
        icon, color = row.mark
        super().__init__(
            row.label,
            icon=icon.icon(color.color(QGuiApplication.palette())),
            tag=row.tag,
            detail=row.aside,
            subtitle=row.detail,
            subtitle_warning=row.detail_is_warning,
            lines=row.info if info_shown else (),
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
