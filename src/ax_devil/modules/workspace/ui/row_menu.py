"""The right-click menu of a browser row."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from ax_devil.modules.workspace.ui.browser_rows import RowAction, RowActionGroup, WorkspaceBrowserRow


def show_row_menu(
    row: WorkspaceBrowserRow, parent: QWidget | None, global_pos: QPoint, pick: Callable[[RowAction], None]
) -> None:
    """Show the menu of *row* at *global_pos* and call *pick* with the action chosen; a row offering none shows
    nothing."""
    menu = build_row_menu(row, parent, pick)
    try:
        if menu.actions():
            menu.exec(global_pos)
    finally:
        menu.deleteLater()


def build_row_menu(row: WorkspaceBrowserRow, parent: QWidget | None, pick: Callable[[RowAction], None]) -> QMenu:
    """Build the menu of *row*: every action it offers, in order, calling *pick* with the one chosen.

    The actions are in groups, split by separators. An action the row offers disabled is listed greyed, with its
    reason as the hover text.
    """
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    previous: RowActionGroup | None = None
    for offered in row.actions:
        action = offered.action
        if previous is not None and action.group is not previous:
            menu.addSeparator()
        previous = action.group
        entry = QAction(action.menu_text, menu)
        entry.setEnabled(not offered.disabled_reason)
        entry.setToolTip(offered.disabled_reason or action.tooltip)
        entry.triggered.connect(lambda _checked=False, action=action: pick(action))
        menu.addAction(entry)
    return menu
