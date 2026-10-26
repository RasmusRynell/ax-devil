"""The **Street** footage: a street running away from the camera, painted by day and by night."""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from ax_devil.modules.catalog_viewer.ground_textures import glow
from ax_devil.modules.catalog_viewer.silhouettes import FONT, SILHOUETTES
from ax_devil.modules.catalog_viewer.street import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    HORIZON,
    lane,
    metre,
    standing,
    street_actors,
)


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


DAY = _Light(
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
NIGHT = _Light(
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


def paint_street(painter: QPainter, light: _Light) -> None:
    rng = random.Random(11)
    sky = QLinearGradient(_point(0, 0), _point(0, HORIZON))
    sky.setColorAt(0, light.sky[0])
    sky.setColorAt(1, light.sky[1])
    painter.fillRect(QRectF(0, 0, FRAME_WIDTH, FRAME_HEIGHT * HORIZON + 1), sky)
    painter.fillRect(QRectF(0, FRAME_HEIGHT * HORIZON, FRAME_WIDTH, FRAME_HEIGHT), light.ground)
    painter.setPen(Qt.PenStyle.NoPen)
    # Distant buildings in the haze at the end of the street.
    for left in np.arange(0.38, 0.62, 0.035).tolist():
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
        for distance in np.arange(4.0, 120.0, 9.0).tolist():
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
            if silhouette := SILHOUETTES.get(actor.name or ""):
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
    for column in np.arange(0.04, 0.8, 0.09).tolist():
        for row in np.arange(0.06, 0.78, 0.12).tolist():
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
        glow(painter, _point(x + 0.6 * scale, ground - 4.9 * scale), 2.2 * scale * FRAME_HEIGHT, QColor(255, 210, 140))
        pool = QRadialGradient(_point(x + 0.6 * scale, ground), 3.0 * scale * FRAME_HEIGHT)
        pool.setColorAt(0, QColor(255, 200, 130, 70))
        pool.setColorAt(1, QColor(255, 200, 130, 0))
        painter.setBrush(QBrush(pool))
        painter.drawEllipse(_point(x + 0.6 * scale, ground), 3.0 * scale * FRAME_HEIGHT, 1.0 * scale * FRAME_HEIGHT)


def _timestamp(painter: QPainter) -> None:
    """Paint the date and camera name a camera burns into its picture."""
    font = QFont(FONT)
    font.setPixelSize(13)
    painter.setFont(font)
    text = "2026-10-03 14:22:07  Main St / Cam 2"
    width = QFontMetricsF(font).horizontalAdvance(text)
    painter.fillRect(QRectF(FRAME_WIDTH - width - 20, FRAME_HEIGHT - 27, width + 12, 19), QColor(0, 0, 0, 150))
    painter.setPen(QColor(240, 240, 240))
    painter.drawText(QPointF(FRAME_WIDTH - width - 14, FRAME_HEIGHT - 13), text)
    painter.setPen(Qt.PenStyle.NoPen)
