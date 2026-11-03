"""Tests for the shared glyph atlas behind Quick text."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont, QFontMetricsF, QImage
from PySide6.QtWidgets import QApplication

from ax_devil.modules.video_player.engine.quick import glyphs
from ax_devil.modules.video_player.engine.quick.glyphs import AtlasFull, FontKey, GlyphAtlas

DPI = 96


def _metrics_width(text: str, pixel_size: float) -> float:
    device = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    device.setDotsPerMeterX(round(DPI / 0.0254))
    device.setDotsPerMeterY(round(DPI / 0.0254))
    font = QFont()
    font.setPointSizeF(pixel_size * 72.0 / DPI)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return QFontMetricsF(font, device).horizontalAdvance(text)


@pytest.mark.parametrize("dpr", [1.0, 2.0])
def test_logical_width_matches_font_metrics(qapp: QApplication, dpr: float) -> None:
    """Text is laid out at its logical size, so a label is as wide at dpr 2 as at dpr 1."""
    atlas = GlyphAtlas(DPI, dpr)
    texts = ["Car 0.93", "Pedestrian walking", "ID 4711 ➜ 12"]
    shaped = atlas.shape(texts, FontKey("", 14.0 * dpr, "regular"))
    for text, width in zip(texts, shaped.width):
        # Advances shaped at device size would be hinted to whole pixels and differ between dprs.
        assert width == pytest.approx(_metrics_width(text, 14.0), abs=0.01)


def test_multiline_rows_stack_below_the_first_baseline(qapp: QApplication) -> None:
    atlas = GlyphAtlas(DPI, 1.0)
    shaped = atlas.shape(["Car\n0.93", "Car"], FontKey("", 14.0, "regular"))
    assert shaped.count[0] > shaped.count[1] > 0
    assert shaped.ink[0, 3] - shaped.ink[1, 3] >= shaped.line_height - 1
    assert shaped.ink[0, 2] - shaped.ink[0, 0] < shaped.width[0] + 2


def test_string_cache_is_bounded_and_a_full_page_must_be_reset(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(glyphs, "MAX_STRINGS", 8)
    monkeypatch.setattr(glyphs, "PAGE_SIZE", 64)
    atlas = GlyphAtlas(DPI, 1.0)
    font = FontKey("", 20.0, "regular")
    kept = atlas.shape(["abc"], font)
    with pytest.raises(AtlasFull):
        for start in range(0, 200, 4):
            atlas.shape([chr(0x0100 + start + offset) for offset in range(4)], font)
            assert atlas._string_count <= 8
    atlas.reset()
    assert atlas.shape(["abc"], font).count[0] == kept.count[0]


def test_snapshot_does_not_change_after_later_writes(qapp: QApplication) -> None:
    atlas = GlyphAtlas(DPI, 1.0)
    font = FontKey("", 14.0, "regular")
    atlas.shape(["abc"], font)
    taken = atlas.snapshot()
    before = taken.copy()
    assert atlas.snapshot() is taken
    atlas.shape(["xyz 123 QWERTY"], font)
    assert atlas.snapshot() is not taken
    assert taken == before
    assert atlas.snapshot() != before
