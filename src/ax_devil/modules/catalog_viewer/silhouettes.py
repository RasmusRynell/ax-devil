"""How the **Street** footage paints the people and vehicles its actors describe, in the colors detection reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
)

from ax_devil.modules.catalog_viewer.street import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    Actor,
    reported_colors,
)
from ax_devil.modules.scene.model import ColorClassification

FONT = "Arial"
"""The typeface of text painted onto the footage."""


def _fabric(attributes: Mapping[str, Any], name: str, fallback: str) -> QColor:
    """Return the color of the first color in *attributes*[*name*], muted as daylight shows fabric and paint."""
    colors: list[ColorClassification] = attributes.get(name) or reported_colors(fallback)
    rgb = colors[0].rgb
    return QColor(int(rgb.r * 0.5 + 60), int(rgb.g * 0.5 + 60), int(rgb.b * 0.5 + 60))


def _pixels(actor: Actor) -> QRectF:
    x, y, w, h = actor.box
    return QRectF(x * FRAME_WIDTH, y * FRAME_HEIGHT, w * FRAME_WIDTH, h * FRAME_HEIGHT)


def _shadow(painter: QPainter, rect: QRectF) -> None:
    painter.setBrush(QColor(0, 0, 0, 70))
    painter.drawEllipse(QPointF(rect.center().x(), rect.bottom()), rect.width() * 0.6, max(1.5, rect.height() * 0.035))


def _paint_person(painter: QPainter, actor: Actor) -> None:
    rect = _pixels(actor)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _shadow(painter, rect)
    painter.setBrush(_fabric(actor.attributes, "lower_clothing_colors", "blue"))
    painter.drawRoundedRect(QRectF(x + w * 0.18, y + h * 0.5, w * 0.29, h * 0.49), w * 0.08, w * 0.08)
    painter.drawRoundedRect(QRectF(x + w * 0.53, y + h * 0.5, w * 0.29, h * 0.49), w * 0.08, w * 0.08)
    painter.setBrush(_fabric(actor.attributes, "upper_clothing_colors", "gray"))
    painter.drawRoundedRect(QRectF(x + w * 0.08, y + h * 0.15, w * 0.84, h * 0.4), w * 0.25, w * 0.25)
    painter.setBrush(QColor(196, 158, 128))
    painter.drawEllipse(QRectF(x + w * 0.3, y + h * 0.01, w * 0.4, h * 0.13))
    painter.setBrush(QColor(52, 40, 32))
    painter.drawChord(QRectF(x + w * 0.3, y + h * 0.005, w * 0.4, h * 0.1), 0, 180 * 16)
    if actor.attributes.get("carries_bag"):
        painter.setBrush(QColor(92, 62, 40))
        painter.drawRoundedRect(QRectF(x + w * 0.78, y + h * 0.4, w * 0.3, h * 0.17), w * 0.05, w * 0.05)


def _paint_car(painter: QPainter, actor: Actor) -> None:
    rect = _pixels(actor)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _shadow(painter, rect)
    body = _fabric(actor.attributes, "vehicle_colors", "gray")
    painter.setBrush(QColor(20, 20, 22))
    painter.drawRect(QRectF(x + w * 0.06, y + h * 0.82, w * 0.16, h * 0.18))
    painter.drawRect(QRectF(x + w * 0.78, y + h * 0.82, w * 0.16, h * 0.18))
    painter.setBrush(body.darker(115))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(x + w * 0.16, y + h * 0.42),
                QPointF(x + w * 0.26, y),
                QPointF(x + w * 0.74, y),
                QPointF(x + w * 0.84, y + h * 0.42),
            ]
        )
    )
    painter.setBrush(QColor(44, 52, 62))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(x + w * 0.21, y + h * 0.4),
                QPointF(x + w * 0.29, y + h * 0.07),
                QPointF(x + w * 0.71, y + h * 0.07),
                QPointF(x + w * 0.79, y + h * 0.4),
            ]
        )
    )
    painter.setBrush(body)
    painter.drawRoundedRect(QRectF(x, y + h * 0.38, w, h * 0.48), w * 0.06, w * 0.06)
    toward = actor.velocity is not None and actor.velocity[1] > 0
    painter.setBrush(QColor(250, 246, 220) if toward else QColor(200, 30, 30))
    painter.drawEllipse(QRectF(x + w * 0.05, y + h * 0.5, w * 0.14, h * 0.1))
    painter.drawEllipse(QRectF(x + w * 0.81, y + h * 0.5, w * 0.14, h * 0.1))
    painter.setBrush(QColor(236, 236, 228))
    painter.drawRect(QRectF(x + w * 0.38, y + h * 0.66, w * 0.24, h * 0.08))


def _paint_bus(painter: QPainter, actor: Actor) -> None:
    rect = _pixels(actor)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _shadow(painter, rect)
    painter.setBrush(_fabric(actor.attributes, "vehicle_colors", "yellow"))
    painter.drawRoundedRect(QRectF(x, y, w, h * 0.95), w * 0.05, w * 0.05)
    painter.setBrush(QColor(40, 48, 58))
    painter.drawRect(QRectF(x + w * 0.06, y + h * 0.16, w * 0.88, h * 0.4))
    painter.setBrush(QColor(20, 20, 20))
    painter.drawRect(QRectF(x + w * 0.15, y + h * 0.03, w * 0.7, h * 0.09))
    font = QFont(FONT)
    font.setPixelSize(max(4, int(h * 0.06)))
    painter.setFont(font)
    painter.setPen(QColor(255, 170, 60))
    painter.drawText(QRectF(x + w * 0.15, y + h * 0.03, w * 0.7, h * 0.09), Qt.AlignmentFlag.AlignCenter, "14 Central")
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(250, 246, 220))
    painter.drawEllipse(QRectF(x + w * 0.06, y + h * 0.75, w * 0.12, h * 0.06))
    painter.drawEllipse(QRectF(x + w * 0.82, y + h * 0.75, w * 0.12, h * 0.06))
    painter.setBrush(QColor(20, 20, 22))
    painter.drawRect(QRectF(x + w * 0.08, y + h * 0.92, w * 0.14, h * 0.08))
    painter.drawRect(QRectF(x + w * 0.78, y + h * 0.92, w * 0.14, h * 0.08))


def _paint_truck(painter: QPainter, actor: Actor) -> None:
    rect = _pixels(actor)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _shadow(painter, rect)
    painter.setBrush(QColor(200, 200, 196))
    painter.drawRect(QRectF(x, y, w, h * 0.62))
    painter.setBrush(_fabric(actor.attributes, "vehicle_colors", "white"))
    painter.drawRect(QRectF(x + w * 0.04, y + h * 0.55, w * 0.92, h * 0.38))
    painter.setBrush(QColor(44, 52, 62))
    painter.drawRect(QRectF(x + w * 0.1, y + h * 0.6, w * 0.8, h * 0.15))
    painter.setBrush(QColor(20, 20, 22))
    painter.drawRect(QRectF(x + w * 0.08, y + h * 0.9, w * 0.16, h * 0.1))
    painter.drawRect(QRectF(x + w * 0.76, y + h * 0.9, w * 0.16, h * 0.1))


def _paint_bike(painter: QPainter, actor: Actor) -> None:
    rect = _pixels(actor)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    _shadow(painter, rect)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(24, 24, 26), max(1.0, w * 0.06)))
    painter.drawEllipse(QRectF(x, y + h * 0.66, w * 0.42, h * 0.33))
    painter.drawEllipse(QRectF(x + w * 0.58, y + h * 0.66, w * 0.42, h * 0.33))
    painter.drawLine(QPointF(x + w * 0.21, y + h * 0.82), QPointF(x + w * 0.5, y + h * 0.6))
    painter.drawLine(QPointF(x + w * 0.5, y + h * 0.6), QPointF(x + w * 0.79, y + h * 0.82))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(60, 66, 90))
    painter.drawRoundedRect(QRectF(x + w * 0.3, y + h * 0.12, w * 0.4, h * 0.42), w * 0.12, w * 0.12)
    painter.setBrush(QColor(196, 158, 128))
    painter.drawEllipse(QRectF(x + w * 0.38, y, w * 0.24, h * 0.12))


SILHOUETTES: Mapping[str, Callable[[QPainter, Actor], None]] = {
    "human": _paint_person,
    "car": _paint_car,
    "bus": _paint_bus,
    "truck": _paint_truck,
    "bike": _paint_bike,
}
"""How the **Street** footage shows each class it has, in the colors detection reports for it."""
