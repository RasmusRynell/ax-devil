"""Which ``RowAction`` a key or a double-click asks of a browser row."""

from __future__ import annotations

from PySide6.QtCore import Qt

from ax_devil.modules.workspace.ui.browser_rows import RowAction

_KEY_ACTIONS = {
    Qt.Key.Key_Return: RowAction.OPEN,
    Qt.Key.Key_Enter: RowAction.OPEN,
    Qt.Key.Key_F2: RowAction.EDIT,
    Qt.Key.Key_Delete: RowAction.REMOVE,
}


def open_action(modifiers: Qt.KeyboardModifier) -> RowAction:
    """Return Open, or Open to the Side when Ctrl is held."""
    return RowAction.OPEN_TO_SIDE if modifiers & Qt.KeyboardModifier.ControlModifier else RowAction.OPEN


def key_action(key: int, modifiers: Qt.KeyboardModifier) -> RowAction | None:
    """Return the action a *key* asks of the selected row, or None for a key that asks none."""
    action = _KEY_ACTIONS.get(Qt.Key(key))
    return open_action(modifiers) if action is RowAction.OPEN else action
