"""Screen-aware initial geometry shared by windows and dialogs."""

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QWidget


def _decorations(window: QWidget) -> QSize:
    """Return the size of the desktop's title bar and borders around *window*, known once its native window exists."""
    window.create()
    return (window.frameGeometry().size() - window.size()).expandedTo(QSize(0, 0))


def bounded_window_size(
    window: QWidget,
    preferred_size: QSize,
    *,
    screen: QScreen | None = None,
    maximum_size: QSize | None = None,
    screen_margin: int | None = None,
) -> QSize:
    """Bound an opening or restored size without changing desktop placement."""
    screen = screen or window.screen()
    size = preferred_size.expandedTo(window.minimumSizeHint()).expandedTo(window.minimumSize())
    if screen is None:
        return size
    margin = window.fontMetrics().height() if screen_margin is None else screen_margin
    bounds = screen.availableGeometry().adjusted(margin, margin, -margin, -margin)
    decorations = _decorations(window)
    limit = bounds.size() - decorations
    if maximum_size is not None:
        limit = limit.boundedTo(maximum_size)
    width = min(size.width(), limit.width())
    layout = window.layout()
    if layout is not None and layout.hasHeightForWidth():
        size.setHeight(max(size.height(), window.heightForWidth(width)))
    return size.boundedTo(limit)


def fit_window(
    window: QWidget,
    preferred_size: QSize,
    *,
    center: bool,
    parent_size_fraction: float | None = 0.85,
    maximum_size: QSize | None = None,
    screen_margin: int | None = None,
) -> None:
    """Fit a window to its screen, optionally sizing it relative to its parent."""
    parent = window.parentWidget()
    anchor = parent.window() if parent is not None else window
    if parent is not None and parent_size_fraction is not None:
        preferred_size = QSize(
            round(anchor.width() * parent_size_fraction),
            round(anchor.height() * parent_size_fraction),
        )
    screen = anchor.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    margin = window.fontMetrics().height() if screen_margin is None else screen_margin
    bounds = available.adjusted(margin, margin, -margin, -margin)
    decorations = _decorations(window)
    size = bounded_window_size(window, preferred_size, screen=screen, maximum_size=maximum_size, screen_margin=margin)
    window.resize(size)
    if parent is None:
        return
    geometry = QRect(window.pos(), window.size() + decorations)
    if center:
        geometry.moveCenter(anchor.frameGeometry().center() if parent is not None else bounds.center())
    geometry.moveLeft(max(bounds.left(), min(geometry.left(), bounds.right() - geometry.width() + 1)))
    geometry.moveTop(max(bounds.top(), min(geometry.top(), bounds.bottom() - geometry.height() + 1)))
    window.move(geometry.topLeft())
