from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


def palette_color_css(palette: QPalette, role: QPalette.ColorRole, *, alpha: float | None = None) -> str:
    """Return a CSS string for the selected palette color."""
    return qcolor_to_css(palette.color(role), alpha=alpha)


def qcolor_to_css(color: QColor, *, alpha: float | None = None) -> str:
    """Return a CSS ``rgba(...)`` or hex string for any ``QColor``.

    When *alpha* is given it overrides the colour's alpha channel.
    When omitted the colour's existing alpha is inspected: translucent
    colours produce ``rgba(...)``; fully-opaque colours produce ``#rrggbb``.
    """
    clone = QColor(color)
    if alpha is not None:
        clone.setAlphaF(alpha)
    if clone.alpha() < 255:
        return f"rgba({clone.red()}, {clone.green()}, {clone.blue()}, {clone.alpha()})"
    return str(clone.name())
