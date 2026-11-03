"""Widget lookups shared by UI tests."""

from PySide6.QtWidgets import QPushButton, QTreeWidgetItem, QWidget


def button(widget: QWidget, text: str) -> QPushButton:
    """Return the push button labelled *text* inside *widget*."""
    return next(button for button in widget.findChildren(QPushButton) if button.text() == text)


def required_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    """Return the child of *item* at *index*, which must exist."""
    child = item.child(index)
    assert child is not None
    return child
