"""One check for text that a window cuts off, shared by every window and dialog."""

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QLabel,
    QStackedWidget,
    QTabWidget,
    QWidget,
)

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

    Cut off means outside the window (except in a scrolling view), or, except for wrapping and elided labels that
    trade width on purpose, narrower than its text needs.
    """
    clipped = []
    for widget in window.findChildren(QWidget):
        if not isinstance(widget, (QAbstractButton, QLabel)) or not widget.text() or not widget.isVisibleTo(window):
            continue
        name = f"{type(widget).__name__} {widget.text()[:30]!r}"
        corners = (widget.rect().topLeft(), widget.rect().bottomRight())
        # A view's content may lie below or beside its viewport, but its text is still cut off when too narrow.
        if not _scrolls(widget, window) and not all(
            window.rect().contains(widget.mapTo(window, corner)) for corner in corners
        ):
            clipped.append(f"{name} lies outside the window")
        trades_width = isinstance(widget, ElidedLabel) or (isinstance(widget, QLabel) and widget.wordWrap())
        needed = widget.minimumSizeHint().width()
        if not trades_width and widget.width() < needed:
            clipped.append(f"{name}: {widget.width()} px of {needed}")
    return clipped


def clipped_text_on_every_page(window: QWidget) -> list[str]:
    """Return `clipped_text_widgets` for each tab and stacked page of *window*, since hidden pages are not checked."""
    clipped = clipped_text_widgets(window)
    for pages in window.findChildren(QTabWidget) + window.findChildren(QStackedWidget):
        index = pages.currentIndex()
        for other in range(pages.count()):
            if other != index:
                pages.setCurrentIndex(other)
                QApplication.processEvents()
                clipped += clipped_text_widgets(window)
        pages.setCurrentIndex(index)
    return clipped
