"""Overlay shader files and the shapes the overlay material draws."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QShader

import ax_devil.modules.video_player.engine.quick as quick_package
from ax_devil.modules.video_player.engine.quick.overlay_vertex import OVERLAY_VERTEX, OverlayKind

from ..conftest import RenderQuads

SHADERS = quick_package.__path__[0] + "/shaders"
RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)


def quad(
    kind: OverlayKind,
    rect: tuple[float, float, float, float],
    fill: tuple[int, ...] = RED,
    pen: tuple[int, ...] = BLUE,
    radius: float = 0,
    stroke: float = 0,
    dash: float = 0,
) -> np.ndarray:
    """One upright shape quad padded by one pixel on every side, like preparation emits."""
    x, y, w, h = rect
    quad_ = np.zeros(4, OVERLAY_VERTEX)
    for index, (cu, cv) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        u, v = -1 + cu * (w + 2), -1 + cv * (h + 2)
        quad_[index] = (x + u, y + v, u, v, w, h, radius, stroke, dash, kind, fill, pen)
    return quad_


def glyph_quad(rect: tuple[int, int, int, int], page_rect: tuple[int, int, int, int], page: QImage) -> np.ndarray:
    """A glyph quad sampling ``page_rect`` of the page, tinted red."""
    x, y, w, h = rect
    px, py, pw, ph = page_rect
    quad_ = np.zeros(4, OVERLAY_VERTEX)
    for index, (cu, cv) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        quad_[index] = (
            x + cu * w,
            y + cv * h,
            (px + cu * pw) / page.width(),
            (py + cv * ph) / page.height(),
            w,
            h,
            0,
            0,
            0,
            OverlayKind.GLYPH,
            RED,
            BLUE,
        )
    return quad_


@pytest.mark.parametrize("name", ["overlay.vert.qsb", "overlay.frag.qsb"])
def test_baked_shader_loads(name: str) -> None:
    with open(f"{SHADERS}/{name}", "rb") as handle:
        shader = QShader.fromSerialized(handle.read())
    assert shader.isValid()


def test_overlay_material_draws_each_kind(render_quads: RenderQuads) -> None:
    page = QImage(8, 8, QImage.Format.Format_Alpha8)
    page.fill(0)
    for y in range(8):
        for x in range(4):
            page.setPixelColor(x, y, QColor(0, 0, 0, 255))
    image = render_quads(
        np.concatenate(
            [
                quad(OverlayKind.ROUNDED_RECT, (0, 0, 20, 20)),
                quad(OverlayKind.ROUNDED_RECT, (20, 0, 20, 20), radius=10),
                quad(OverlayKind.ELLIPSE, (40, 0, 20, 20)),
                quad(OverlayKind.ELLIPSE, (40.5, 40.5, 20, 20)),
                quad(OverlayKind.ROUNDED_RECT, (60, 0, 20, 20), fill=(0, 0, 0, 0), stroke=2, dash=12),
                glyph_quad((80, 0, 8, 8), (0, 0, 8, 8), page),
            ]
        ),
        100,
        page,
    )

    background = image.pixelColor(99, 99)

    def covered(x: int, y: int) -> bool:
        return image.pixelColor(x, y) != background

    assert image.pixelColor(10, 10) == QColor(255, 0, 0)
    assert covered(0, 0)  # square corner
    assert not covered(21, 0)  # rounded corner
    assert image.pixelColor(30, 10) == QColor(255, 0, 0)
    assert not covered(41, 0)  # ellipse corner
    assert image.pixelColor(50, 50) == QColor(255, 0, 0)  # circle centre, where the distance formula is 0/0
    assert image.pixelColor(50, 10) == QColor(255, 0, 0)
    top_edge = [covered(x, 0) for x in range(60, 80)]  # dashes: 6 px on, 6 px off
    assert top_edge[3] and not top_edge[9] and top_edge[15]
    assert not covered(70, 10)  # unfilled centre
    assert image.pixelColor(81, 4) == QColor(255, 0, 0)  # covered half of the glyph page
    assert not covered(86, 4)
