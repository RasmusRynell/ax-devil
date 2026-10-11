"""One check for text that a window cuts off, shared by every window and dialog."""

from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QLabel, QWidget

from ax_devil.modules.chrome.elided_label import ElidedLabel


def clipped_text_widgets(window: QWidget) -> list[str]:
    """Return a description of each shown button or label with text whose width is below what its text needs.

    Wrapping labels, elided labels and anything inside a scrolling view are skipped: they trade width on purpose.
    """
    clipped = []
    for widget in window.findChildren(QWidget):
        if not isinstance(widget, (QAbstractButton, QLabel)) or not widget.text() or not widget.isVisibleTo(window):
            continue
        if isinstance(widget, ElidedLabel) or (isinstance(widget, QLabel) and widget.wordWrap()):
            continue
        parent = widget.parentWidget()
        while parent is not None and parent is not window:
            if isinstance(parent, QAbstractScrollArea):
                break
            parent = parent.parentWidget()
        else:
            needed = widget.minimumSizeHint().width()
            if widget.width() < needed:
                clipped.append(f"{type(widget).__name__} {widget.text()!r}: {widget.width()} px of {needed}")
    return clipped
