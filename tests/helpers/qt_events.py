"""Qt input events built the same way by several test files."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent


def make_wheel_event(position: QPointF, delta_y: int) -> QWheelEvent:
    """Build a mouse-wheel event at *position*; 120 is one notch of zoom-in."""
    return QWheelEvent(
        QPointF(position),
        QPointF(position),
        QPoint(),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
