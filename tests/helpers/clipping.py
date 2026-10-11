"""One check for text that a window cuts off, shared by every window and dialog."""

from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QLabel, QWidget

from ax_devil.modules.chrome.elided_label import ElidedLabel


def _scrolls(widget: QWidget, window: QWidget) -> bool:
    """Return whether *widget* sits in a scrolling view of *window*, where overflow is reachable by scrolling."""
    parent = widget.parentWidget()
    while parent is not None and parent is not window:
        if isinstance(parent, QAbstractScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def clipped_text_widgets(window: QWidget) -> list[str]:
    """Return a description of each shown button or label with text that is cut off.

    Cut off means outside the window, or, except for wrapping and elided labels that trade width on purpose, narrower
    than its text needs. Anything inside a scrolling view is skipped.
    """
    clipped = []
    for widget in window.findChildren(QWidget):
        if not isinstance(widget, (QAbstractButton, QLabel)) or not widget.text() or not widget.isVisibleTo(window):
            continue
        if _scrolls(widget, window):
            continue
        name = f"{type(widget).__name__} {widget.text()[:30]!r}"
        corners = (widget.rect().topLeft(), widget.rect().bottomRight())
        if not all(window.rect().contains(widget.mapTo(window, corner)) for corner in corners):
            clipped.append(f"{name} lies outside the window")
        trades_width = isinstance(widget, ElidedLabel) or (isinstance(widget, QLabel) and widget.wordWrap())
        needed = widget.minimumSizeHint().width()
        if not trades_width and widget.width() < needed:
            clipped.append(f"{name}: {widget.width()} px of {needed}")
    return clipped
