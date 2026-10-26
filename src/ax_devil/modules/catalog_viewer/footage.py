"""Footage-like backgrounds the example sheets are drawn over, painted with QPainter from fixed seeds.

``grid_background`` tiles bright, busy, grey and dark ground, each kind once per row and column of a
``GRID_SIZE`` by ``GRID_SIZE`` grid. The street backgrounds show the street of ``street.py`` by day, with or without
the people and traffic its actors describe, and empty at night. Each is painted once per process.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import replace
from functools import cache

import numpy as np
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient

from ax_devil.modules.catalog_viewer.ground_textures import TEXTURES
from ax_devil.modules.catalog_viewer.street import FRAME_HEIGHT, FRAME_WIDTH
from ax_devil.modules.catalog_viewer.street_scene import DAY, NIGHT, paint_street

GRID_SIZE = 4
"""Cells across and down the situation grid; the sheets place one situation in each."""


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
            TEXTURES[(row + column) % len(TEXTURES)](painter, rect, random.Random(index))
            painter.restore()

    return _frame(paint)


@cache
def day_street_background() -> QImage:
    """Return the **Street** footage: a sunny street with the people and traffic its Scene reports."""
    return _frame(lambda painter: paint_street(painter, DAY))


@cache
def empty_street_background() -> QImage:
    """Return the sunny street with nobody on it, for examples that are not where the footage shows something."""
    return _frame(lambda painter: paint_street(painter, replace(DAY, people=False)))


@cache
def night_street_background() -> QImage:
    """Return the same street at night, empty, lit by street lights and windows."""
    return _frame(lambda painter: paint_street(painter, NIGHT))
