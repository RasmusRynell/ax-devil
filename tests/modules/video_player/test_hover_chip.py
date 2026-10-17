"""The hover chip stays inside the viewer and out of the way of the object it names."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.engine.data_types import HoverHit
from ax_devil.modules.video_player.ui.hover_chip import HoverChip


def _hit(label: str = "12 person", detail: str = "conf 0.97") -> HoverHit:
    return HoverHit("12", (0.0, 0.0, 0.1, 0.1), label, detail, "person")


def _viewer(qtbot: QtBot, width: int, height: int) -> tuple[QWidget, HoverChip]:
    viewer = QWidget()
    viewer.resize(width, height)
    qtbot.addWidget(viewer)
    return viewer, HoverChip(viewer)


@pytest.mark.parametrize(
    ("avoid", "anchor"),
    [((260, 100, 40, 40), (300, 100)), ((120, 200, 40, 20), (160, 200))],  # Most room on the left, then above.
)
def test_chip_sits_inside_the_viewer_beside_its_object(
    qtbot: QtBot, avoid: tuple[int, int, int, int], anchor: tuple[int, int]
) -> None:
    viewer, chip = _viewer(qtbot, 320, 240)

    chip.show_for(_hit(), *anchor, avoid_rect=avoid)

    assert viewer.rect().contains(chip.geometry())
    assert not chip.geometry().intersects(QRect(*avoid))


def test_long_text_is_elided_to_fit_a_narrow_viewer(qtbot: QtBot) -> None:
    viewer, chip = _viewer(qtbot, 160, 120)

    chip.show_for(_hit("12d872dd-1285-536d-bc40-123456789abc person", "conf 0.97 · speed 1.2"), 100, 20)

    assert viewer.rect().contains(chip.geometry())
