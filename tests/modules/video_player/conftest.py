"""Fixtures that draw overlay quads through the real scene graph."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.quick.overlay_material import OverlayItem
from ax_devil.modules.video_player.engine.quick.preparation import PreparedDrawing
from ax_devil.modules.video_player.engine.quick.surface import QuickSurface

RenderQuads = Callable[..., QImage]


@pytest.fixture
def render_quads(qtbot: QtBot) -> RenderQuads:
    """Return ``render(vertices, size, page=None)``: the quads drawn over black on a *size* px square at dpr 1."""

    def render(vertices: np.ndarray, size: int, page: QImage | None = None) -> QImage:
        surface = QuickSurface()
        qtbot.addWidget(surface, before_close_func=lambda widget: widget.cleanup())
        surface.setClearColor(QColor("black"))
        surface.resize(size, size)
        surface.show()
        qtbot.waitExposed(surface)
        root = surface.rootObject()
        item = OverlayItem(root)
        item.setSize(root.size())
        item.present(PreparedDrawing(vertices, page), 1.0)
        QApplication.processEvents()
        return surface.grabFramebuffer()

    return render
