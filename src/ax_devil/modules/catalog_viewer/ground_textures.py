"""The four kinds of ground the situation grid is painted over, each a QPainter texture from a fixed seed."""

from __future__ import annotations

import random
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)

_Texture = Callable[[QPainter, QRectF, random.Random], None]


def _bright(painter: QPainter, rect: QRectF, rng: random.Random) -> None:
    """Overexposed concrete or snow: nearly white, with faint joints and blown-out patches."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, QColor(250, 250, 246))
    gradient.setColorAt(1, QColor(214, 212, 204))
    painter.fillRect(rect, gradient)
    painter.setPen(QPen(QColor(188, 186, 178), 1.5))
    step = rng.uniform(60, 90)
    offset = rng.uniform(0, step)
    for index in range(int(rect.width() / step) + 1):
        x = rect.left() + offset + index * step
        painter.drawLine(QPointF(x, rect.top()), QPointF(x - 30, rect.bottom()))
    painter.setPen(Qt.PenStyle.NoPen)
    for _ in range(4):
        painter.setBrush(QColor(255, 255, 255, 150))
        center = QPointF(rect.left() + rng.random() * rect.width(), rect.top() + rng.random() * rect.height())
        painter.drawEllipse(center, rng.uniform(30, 80), rng.uniform(15, 40))


def _foliage(painter: QPainter, rect: QRectF, rng: random.Random) -> None:
    """Hedges and trees: clumps of leaves, lit on top and dark between, a busy high-contrast texture."""
    painter.fillRect(rect, QColor(24, 36, 20))
    painter.setPen(Qt.PenStyle.NoPen)
    shades = ((34, 58, 26), (56, 84, 40), (84, 114, 56), (120, 150, 78), (176, 194, 126))
    for _ in range(int(rect.width() * rect.height() / 1500)):
        cx, cy = rect.left() + rng.random() * rect.width(), rect.top() + rng.random() * rect.height()
        radius = rng.uniform(14, 34)
        for _ in range(36):
            dx, dy = rng.gauss(0, 0.45), rng.gauss(0, 0.45)
            # Leaves facing up catch the light; those underneath stay in shade.
            painter.setBrush(QColor(*shades[max(0, min(4, int(2.4 - dy * 2.5 + rng.gauss(0, 0.8))))]))
            painter.drawEllipse(QPointF(cx + dx * radius, cy + dy * radius), rng.uniform(2, 6), rng.uniform(1.5, 4.5))


def _asphalt(painter: QPainter, rect: QRectF, rng: random.Random) -> None:
    """Mid-grey asphalt with a dashed lane marking and a stain."""
    painter.fillRect(rect, QColor(112, 114, 118))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(90, 92, 96, 120))
    painter.drawEllipse(QPointF(rect.left() + rect.width() * 0.3, rect.top() + rect.height() * 0.7), 40, 18)
    painter.setBrush(QColor(236, 236, 224))
    x = rect.left() + rect.width() * rng.uniform(0.55, 0.7)
    for y in np.arange(rect.top() - rng.uniform(0, 60), rect.bottom(), 70.0).tolist():
        painter.drawPolygon(
            QPolygonF([QPointF(x, y), QPointF(x + 7, y), QPointF(x + 12, y + 38), QPointF(x + 5, y + 38)])
        )


def _night(painter: QPainter, rect: QRectF, rng: random.Random) -> None:
    """A dark scene with street lights, lit windows and their glow."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, QColor(10, 12, 22))
    gradient.setColorAt(1, QColor(30, 26, 22))
    painter.fillRect(rect, gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    for _ in range(int(rect.width() * rect.height() / 4000) + 4):
        center = QPointF(rect.left() + rng.random() * rect.width(), rect.top() + rng.random() * rect.height())
        glow(painter, center, rng.uniform(8, 36), QColor(255, 212, 150))


def glow(painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
    """Paint a point light: a bright core fading into the dark."""
    glow = QRadialGradient(center, radius)
    glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 200))
    glow.setColorAt(0.15, QColor(color.red(), color.green(), color.blue(), 110))
    glow.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(center, radius, radius)
    painter.setBrush(QColor(255, 248, 230))
    painter.drawEllipse(center, max(1.5, radius * 0.08), max(1.5, radius * 0.08))


TEXTURES: tuple[_Texture, ...] = (_bright, _foliage, _asphalt, _night)
