"""Footage-like backgrounds the example sheets are drawn over, painted with QPainter from fixed seeds.

``grid_background`` tiles bright, busy, grey and dark ground, each kind once per row and column of a
``GRID_SIZE`` by ``GRID_SIZE`` grid. The street backgrounds show the street of ``street.py`` by day, with or without
the people and traffic its actors describe, and empty at night. Each is painted once per process.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import cache
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from ax_devil.modules.catalog_viewer.street import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    HORIZON,
    Actor,
    lane,
    metre,
    reported_colors,
    standing,
    street_actors,
)
from ax_devil.modules.scene.model import ColorClassification

GRID_SIZE = 4
"""Cells across and down the situation grid; the sheets place one situation in each."""
_FONT = "Arial"

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
    for y in np.arange(rect.top() - rng.uniform(0, 60), rect.bottom(), 70.0):
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
        _glow(painter, center, rng.uniform(8, 36), QColor(255, 212, 150))


def _glow(painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
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


_TEXTURES: tuple[_Texture, ...] = (_bright, _foliage, _asphalt, _night)


def _frame(paint: Callable[[QPainter], None]) -> QImage:
    """Return a frame painted by *paint*, with a camera's sensor grain and darker corners."""
    image = QImage(FRAME_WIDTH, FRAME_HEIGHT, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint(painter)
    noise = np.random.default_rng(3).integers(0, 256, (FRAME_HEIGHT, FRAME_WIDTH), dtype=np.uint8)
    grain = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 4), dtype=np.uint8)
    grain[..., 0:3] = noise[..., None]
    grain[..., 3] = 14
    grain_image = QImage(grain.data, FRAME_WIDTH, FRAME_HEIGHT, FRAME_WIDTH * 4, QImage.Format.Format_RGBA8888)
    painter.drawImage(0, 0, grain_image)
    vignette = QRadialGradient(QPointF(FRAME_WIDTH / 2, FRAME_HEIGHT / 2), FRAME_WIDTH * 0.75)
    vignette.setColorAt(0.6, QColor(0, 0, 0, 0))
    vignette.setColorAt(1, QColor(0, 0, 0, 70))
    painter.fillRect(image.rect(), vignette)
    painter.end()
    return image


@cache
def grid_background() -> QImage:
    """Return the situation grid's ground: each cell bright, busy, grey or dark, each kind once per row and column."""

    def paint(painter: QPainter) -> None:
        width, height = FRAME_WIDTH / GRID_SIZE, FRAME_HEIGHT / GRID_SIZE
        for index in range(GRID_SIZE * GRID_SIZE):
            row, column = divmod(index, GRID_SIZE)
            rect = QRectF(column * width, row * height, width, height)
            painter.save()
            painter.setClipRect(rect)
            _TEXTURES[(row + column) % len(_TEXTURES)](painter, rect, random.Random(index))
            painter.restore()

    return _frame(paint)


@dataclass(frozen=True, slots=True)
class _Light:
    """The colors of the street at one time of day."""

    sky: tuple[QColor, QColor]
    ground: QColor
    road: tuple[QColor, QColor]
    marking: QColor
    shade: int
    facades: tuple[QColor, QColor]
    window: QColor
    lit_windows: float
    lamps: bool
    people: bool
    leaves: int
    """How much darker than in daylight trees look, in percent."""


_DAY = _Light(
    (QColor(188, 208, 230), QColor(252, 252, 250)),
    QColor(214, 210, 200),
    (QColor(150, 152, 156), QColor(98, 100, 104)),
    QColor(240, 240, 232),
    120,
    (QColor(70, 66, 66), QColor(232, 224, 206)),
    QColor(150, 172, 196),
    0.0,
    False,
    True,
    100,
)
_NIGHT = _Light(
    (QColor(6, 8, 18), QColor(30, 32, 48)),
    QColor(46, 44, 42),
    (QColor(34, 34, 38), QColor(24, 24, 28)),
    QColor(120, 120, 112),
    60,
    (QColor(22, 20, 22), QColor(36, 34, 34)),
    QColor(20, 24, 32),
    0.35,
    True,
    False,
    420,
)


def _point(x: float, y: float) -> QPointF:
    return QPointF(x * FRAME_WIDTH, y * FRAME_HEIGHT)


def _toward_horizon(x: float, y: float, share: float) -> tuple[float, float]:
    """Return the point *share* of the way from (*x*, *y*) to where the street vanishes."""
    return x + (0.5 - x) * share, y + (HORIZON - y) * share


def _polygon(*points: tuple[float, float]) -> QPolygonF:
    return QPolygonF([_point(x, y) for x, y in points])


def _paint_street(painter: QPainter, light: _Light) -> None:
    rng = random.Random(11)
    sky = QLinearGradient(_point(0, 0), _point(0, HORIZON))
    sky.setColorAt(0, light.sky[0])
    sky.setColorAt(1, light.sky[1])
    painter.fillRect(QRectF(0, 0, FRAME_WIDTH, FRAME_HEIGHT * HORIZON + 1), sky)
    painter.fillRect(QRectF(0, FRAME_HEIGHT * HORIZON, FRAME_WIDTH, FRAME_HEIGHT), light.ground)
    painter.setPen(Qt.PenStyle.NoPen)
    # Distant buildings in the haze at the end of the street.
    for left in np.arange(0.38, 0.62, 0.035):
        top = HORIZON - rng.uniform(0.04, 0.11)
        haze = light.sky[1].darker(rng.randint(108, 125))
        painter.fillRect(QRectF(_point(left, top), _point(left + 0.033, HORIZON + 0.002)), haze)
    # The road, with its lanes and a pedestrian crossing near the camera.
    road = QLinearGradient(_point(0, HORIZON), _point(0, 1))
    road.setColorAt(0, light.road[0])
    road.setColorAt(1, light.road[1])
    painter.setBrush(QBrush(road))
    painter.drawPolygon(_polygon((0.16, 1.0), (0.88, 1.0), (0.505, HORIZON), (0.495, HORIZON)))
    _asphalt_grain(painter, rng)
    painter.setBrush(light.marking)
    for bottom in (0.4, 0.64):
        # Three metres of paint every nine metres, from four metres in front of the camera.
        for distance in np.arange(4.0, 120.0, 9.0):
            near, far = HORIZON + (1 - HORIZON) * 4 / distance, HORIZON + (1 - HORIZON) * 4 / (distance + 3)
            half_near, half_far = 0.006 * (near - HORIZON), 0.006 * (far - HORIZON)
            x_near, x_far = lane(bottom, near), lane(bottom, far)
            painter.drawPolygon(
                _polygon(
                    (x_near - half_near, near),
                    (x_near + half_near, near),
                    (x_far + half_far, far),
                    (x_far - half_far, far),
                )
            )
    for stripe in range(9):
        share = 0.06 + stripe * 0.1
        left, right = 0.16 + 0.72 * share, 0.16 + 0.72 * (share + 0.055)
        top_share = 1 - (0.8 - HORIZON) / (1 - HORIZON)
        bottom_share = 1 - (0.92 - HORIZON) / (1 - HORIZON)
        painter.drawPolygon(
            _polygon(
                (left + (0.5 - left) * bottom_share, 0.92),
                (right + (0.5 - right) * bottom_share, 0.92),
                (right + (0.5 - right) * top_share, 0.8),
                (left + (0.5 - left) * top_share, 0.8),
            )
        )
    # Buildings: the left one in shade, the right one in the sun.
    for side, color in ((0.0, light.facades[0]), (1.0, light.facades[1])):
        near_bottom = 0.86 if side == 0.0 else 0.8
        far_top, far_bottom = _toward_horizon(side, -0.05, 0.84), _toward_horizon(side, near_bottom, 0.84)
        painter.setBrush(color)
        painter.drawPolygon(_polygon((side, -0.05), far_top, far_bottom, (side, near_bottom)))
        _windows(painter, rng, light, side, near_bottom)
    # The left building's shadow across its pavement and part of the road.
    painter.setBrush(QColor(0, 0, 0, light.shade))
    painter.drawPolygon(
        _polygon((0.0, 0.86), _toward_horizon(0.0, 0.86, 0.84), (0.49, HORIZON + 0.01), (0.33, 1.0), (0.0, 1.0))
    )
    for ground in (0.43, 0.5, 0.62):
        _tree(painter, rng, light, lane(1.02, ground), ground)
    for ground in (0.4, 0.47, 0.6, 0.83):
        _lamp(painter, light, lane(0.08, ground), ground)
    if light.people:
        for actor in street_actors():
            # Heads belong to their person, and motion without a class has no shape of its own.
            if silhouette := _SILHOUETTES.get(actor.name or ""):
                silhouette(painter, actor)
    _timestamp(painter)


def _asphalt_grain(painter: QPainter, rng: random.Random) -> None:
    painter.setBrush(QColor(0, 0, 0, 28))
    for _ in range(160):
        y = rng.uniform(HORIZON + 0.05, 1.0)
        x = rng.uniform(0.2, 0.85)
        painter.drawEllipse(_point(x, y), rng.uniform(2, 14) * (y - HORIZON), rng.uniform(1, 4) * (y - HORIZON))


def _windows(painter: QPainter, rng: random.Random, light: _Light, side: float, near_bottom: float) -> None:
    """Paint rows of windows along a facade, and lit shop windows at street level."""
    for column in np.arange(0.04, 0.8, 0.09):
        for row in np.arange(0.06, 0.78, 0.12):
            corners = []
            for share, height in (
                (column, row),
                (column + 0.05, row),
                (column + 0.05, row + 0.07),
                (column, row + 0.07),
            ):
                top, bottom = _toward_horizon(side, -0.05, share), _toward_horizon(side, near_bottom, share)
                corners.append((top[0], top[1] + (bottom[1] - top[1]) * height))
            lit = rng.random() < light.lit_windows
            painter.setBrush(QColor(255, 206, 140) if lit else light.window.darker(rng.randint(95, 130)))
            painter.drawPolygon(_polygon(*corners))
        shop = []
        for share, height in ((column, 0.84), (column + 0.07, 0.84), (column + 0.07, 0.97), (column, 0.97)):
            top, bottom = _toward_horizon(side, -0.05, share), _toward_horizon(side, near_bottom, share)
            shop.append((top[0], top[1] + (bottom[1] - top[1]) * height))
        painter.setBrush(QColor(255, 214, 160, 150 if side == 0.0 or light.lamps else 60))
        painter.drawPolygon(_polygon(*shop))


def _tree(painter: QPainter, rng: random.Random, light: _Light, x: float, ground: float) -> None:
    """Paint a street tree: a trunk and a dense crown of leaves about four metres up."""
    scale = metre(ground)
    across = scale * FRAME_HEIGHT / FRAME_WIDTH
    trunk = standing(x, ground, (0.3, 3.2))
    painter.setBrush(QColor(58, 44, 34).darker(light.leaves))
    painter.drawRect(QRectF(_point(trunk[0], trunk[1]), _point(trunk[0] + trunk[2], ground)))
    leaves = ((34, 58, 26), (56, 84, 40), (84, 114, 56), (120, 150, 78), (176, 194, 126), (22, 34, 18))
    for _ in range(int(2500 * scale) + 120):
        painter.setBrush(QColor(*rng.choice(leaves)).darker(light.leaves))
        dx, dy = rng.uniform(-1, 1), rng.uniform(-1, 1)
        if dx * dx + dy * dy > 1:
            continue
        center = _point(x + dx * 1.9 * across, ground - (4.6 + dy * 1.7) * scale)
        size = rng.uniform(0.08, 0.22) * scale * FRAME_HEIGHT
        painter.drawEllipse(center, size, size * rng.uniform(0.6, 1.0))


def _lamp(painter: QPainter, light: _Light, x: float, ground: float) -> None:
    scale = metre(ground)
    painter.setPen(QPen(QColor(40, 42, 46), max(1.0, 0.12 * scale * FRAME_HEIGHT)))
    painter.drawLine(_point(x, ground), _point(x, ground - 5.0 * scale))
    painter.drawLine(_point(x, ground - 5.0 * scale), _point(x + 0.6 * scale, ground - 5.0 * scale))
    painter.setPen(Qt.PenStyle.NoPen)
    if light.lamps:
        _glow(painter, _point(x + 0.6 * scale, ground - 4.9 * scale), 2.2 * scale * FRAME_HEIGHT, QColor(255, 210, 140))
        pool = QRadialGradient(_point(x + 0.6 * scale, ground), 3.0 * scale * FRAME_HEIGHT)
        pool.setColorAt(0, QColor(255, 200, 130, 70))
        pool.setColorAt(1, QColor(255, 200, 130, 0))
        painter.setBrush(QBrush(pool))
        painter.drawEllipse(_point(x + 0.6 * scale, ground), 3.0 * scale * FRAME_HEIGHT, 1.0 * scale * FRAME_HEIGHT)


def _timestamp(painter: QPainter) -> None:
    """Paint the date and camera name a camera burns into its picture."""
    font = QFont(_FONT)
    font.setPixelSize(13)
    painter.setFont(font)
    text = "2026-10-03 14:22:07  Main St / Cam 2"
    width = QFontMetricsF(font).horizontalAdvance(text)
    painter.fillRect(QRectF(FRAME_WIDTH - width - 20, FRAME_HEIGHT - 27, width + 12, 19), QColor(0, 0, 0, 150))
    painter.setPen(QColor(240, 240, 240))
    painter.drawText(QPointF(FRAME_WIDTH - width - 14, FRAME_HEIGHT - 13), text)
    painter.setPen(Qt.PenStyle.NoPen)


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
    font = QFont(_FONT)
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


_SILHOUETTES: Mapping[str, Callable[[QPainter, Actor], None]] = {
    "human": _paint_person,
    "car": _paint_car,
    "bus": _paint_bus,
    "truck": _paint_truck,
    "bike": _paint_bike,
}
"""How the **Street** footage shows each class it has, in the colors detection reports for it."""


@cache
def day_street_background() -> QImage:
    """Return the **Street** footage: a sunny street with the people and traffic its Scene reports."""
    return _frame(lambda painter: _paint_street(painter, _DAY))


@cache
def empty_street_background() -> QImage:
    """Return the sunny street with nobody on it, for examples that are not where the footage shows something."""
    return _frame(lambda painter: _paint_street(painter, replace(_DAY, people=False)))


@cache
def night_street_background() -> QImage:
    """Return the same street at night, empty, lit by street lights and windows."""
    return _frame(lambda painter: _paint_street(painter, _NIGHT))
