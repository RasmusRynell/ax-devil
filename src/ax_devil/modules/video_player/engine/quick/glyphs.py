"""Glyph atlas: shape each distinct string once and keep its glyph bitmaps on one shared Alpha8 page.

GUI thread only. Per frame, ``GlyphAtlas.shape`` costs one dict lookup per distinct string; only strings it has not
seen run through QTextLayout at the logical font size, so lengths do not depend on the device pixel ratio; glyph
bitmaps are drawn at device size. Glyph origins snap to whole device pixels, so equal strings always produce equal
bitmaps and only the bitmaps are cached, never per-frame text rendering. A full page raises ``AtlasFull``; the caller
resets the atlas (``reset``) and lays out again. If even a fresh page is too small, it sets ``drop_overflow`` so the
glyphs that do not fit are left out (counted in ``dropped``) instead of failing the frame.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QRawFont, QTextLayout, QTextOption, QTransform

from ax_devil.modules.settings.logging_config import get_logger

if TYPE_CHECKING:
    from ax_devil.modules.video_player.engine.drawing import LabelWeight

PAGE_SIZE = 2048
MAX_STRINGS = 16384
_MAX_FONTS = 256

GLYPH = np.dtype([("dx", "i4"), ("dy", "i4"), ("w", "u2"), ("h", "u2"), ("u", "u2"), ("v", "u2")])
"""One glyph quad: device-pixel offset from the snapped baseline origin and its rectangle on the atlas page."""

LINE_BREAKS = re.compile(r"\r\n|[\r\n  ]")
_WEIGHTS: dict[str, QFont.Weight] = {
    "regular": QFont.Weight.Normal,
    "medium": QFont.Weight.Medium,
    "semibold": QFont.Weight.DemiBold,
    "bold": QFont.Weight.Bold,
}
_NO_INK = np.zeros(4, np.float32)


@dataclass(frozen=True, slots=True)
class FontKey:
    """A font by family (empty for the application font), size in device pixels and label weight."""

    family: str
    pixel_size: float
    weight: LabelWeight


@dataclass(frozen=True, slots=True)
class ShapedTexts:
    """Shaped rows in input order. Lengths are logical pixels; ``glyphs`` offsets are device pixels.

    ``ink`` rows are left, top, right, bottom relative to the first baseline origin. Row *i* owns
    ``glyphs[start[i]:start[i] + count[i]]``; the arrays stay valid after the atlas forgets its strings.
    """

    width: NDArray[np.float32]
    ink: NDArray[np.float32]
    ascent: float
    descent: float
    line_height: float
    start: NDArray[np.intp]
    count: NDArray[np.intp]
    glyphs: NDArray[np.void]


class AtlasFull(Exception):
    """The atlas page has no room for a glyph; internal to preparation, which resets the atlas and lays out again."""


@dataclass(frozen=True, slots=True)
class _Slot:
    u: int
    v: int
    w: int
    h: int
    left: int
    top: int


@dataclass(frozen=True, slots=True)
class _FontSetup:
    font: QFont
    device: QImage
    ascent: float
    descent: float
    line_height: float


def _warn_oversized_font(pixel_size: float) -> None:
    """Log, once per process, that text was skipped because its font is larger than the atlas page."""
    global _warned_oversized
    if not _warned_oversized:
        _warned_oversized = True
        logger.warning(f"Text in a {pixel_size:.0f} px font is larger than the glyph atlas page and is not drawn")


_warned_oversized = False
logger = get_logger(__name__)


class GlyphAtlas:
    """Shaped strings and their glyph bitmaps for one (dpi, device pixel ratio)."""

    def __init__(self, dpi: int, dpr: float) -> None:
        """Create an empty atlas whose fonts resolve at *dpi* and whose lengths are divided by *dpr*."""
        self._dpi = dpi
        self._dpr = dpr
        self._fonts: dict[FontKey, _FontSetup] = {}
        self._strings: dict[FontKey, dict[str, int]] = {}
        self._slots: dict[tuple[str, str, int, float, int], _Slot | None] = {}
        self._snapshot: QImage | None = None
        self._arena: NDArray[np.void] = np.empty(0, GLYPH)
        self._spans: NDArray[np.intp] = np.zeros((0, 2), np.intp)
        self._floats: NDArray[np.float32] = np.zeros((0, 5), np.float32)
        self.reset()

    def shape(self, texts: Sequence[str] | NDArray[np.object_], font: FontKey) -> ShapedTexts:
        """Shape *texts* in *font*. Only strings not seen before are laid out."""
        setup = self._setup(font)
        strings = list(texts)
        index = self._rows(font, setup, strings)
        spans = self._spans[index]
        floats = self._floats[index]
        glyphs = self._arena[: self._glyph_count]
        glyphs.flags.writeable = False
        return ShapedTexts(
            width=floats[:, 0],
            ink=floats[:, 1:],
            ascent=setup.ascent,
            descent=setup.descent,
            line_height=setup.line_height,
            start=spans[:, 0],
            count=spans[:, 1],
            glyphs=glyphs,
        )

    def snapshot(self) -> QImage:
        """Return the page as it is now (a shallow copy, so later glyph writes never change it)."""
        if self._snapshot is None:
            self._snapshot = QImage(self._page)
        return self._snapshot

    def reset(self) -> None:
        """Forget every glyph and string on a fresh page; earlier snapshots keep the old one."""
        self._page = QImage(PAGE_SIZE, PAGE_SIZE, QImage.Format.Format_Alpha8)
        self._page.fill(0)
        self._slots.clear()
        self._clear_strings()
        self._snapshot = None
        self._shelf = (0, 0, 0)
        self.drop_overflow = False
        self.dropped = 0

    def _setup(self, key: FontKey) -> _FontSetup:
        setup = self._fonts.get(key)
        if setup is None:
            if len(self._fonts) >= _MAX_FONTS:
                self._fonts.clear()
            font = QFont(key.family) if key.family else QFont()
            # Text is laid out at its logical size with unhinted advances, so it has the same width at any device pixel
            # ratio; only the glyph bitmaps are drawn at device size.
            font.setPointSizeF(key.pixel_size / self._dpr * 72.0 / self._dpi)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            font.setWeight(_WEIGHTS[key.weight])
            device = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            device.setDotsPerMeterX(round(self._dpi / 0.0254))
            device.setDotsPerMeterY(round(self._dpi / 0.0254))
            metrics = QFontMetricsF(font, device)
            setup = _FontSetup(font, device, metrics.ascent(), metrics.descent(), metrics.lineSpacing())
            self._fonts[key] = setup
        return setup

    def _rows(self, key: FontKey, setup: _FontSetup, strings: list[str]) -> NDArray[np.intp]:
        """Return each text's row in the span and float tables, shaping and storing the ones not seen yet."""
        cache = self._strings.setdefault(key, {})
        found = list(map(cache.get, strings))
        if None in found:
            missing = dict.fromkeys(text for text, row in zip(strings, found) if row is None)
            if self._string_count + len(missing) > MAX_STRINGS:
                self._clear_strings()
                cache = self._strings.setdefault(key, {})
                missing = dict.fromkeys(strings)
            for text in missing:
                cache[text] = self._store(*self._build(text, setup))
                self._string_count += 1
            found = list(map(cache.__getitem__, strings))
        return np.array(found, np.intp)

    def _store(self, glyphs: NDArray[np.void], width: float, ink: NDArray[np.float32]) -> int:
        """Append one shaped string to the glyph arena and the tables; return its row."""
        count = len(glyphs)
        if self._glyph_count + count > len(self._arena):
            grown = np.empty(max(2 * len(self._arena), self._glyph_count + count), GLYPH)
            grown[: self._glyph_count] = self._arena[: self._glyph_count]
            self._arena = grown
        self._arena[self._glyph_count : self._glyph_count + count] = glyphs
        row = self._row_count
        if row == len(self._spans):
            self._spans = np.concatenate([self._spans, np.zeros_like(self._spans)])
            self._floats = np.concatenate([self._floats, np.zeros_like(self._floats)])
        self._spans[row] = (self._glyph_count, count)
        self._floats[row, 0] = width
        self._floats[row, 1:] = ink
        self._glyph_count += count
        self._row_count += 1
        return row

    def _clear_strings(self) -> None:
        """Forget every shaped string. Results already returned keep their own arrays."""
        self._strings.clear()
        self._string_count = 0
        self._row_count = 0
        self._glyph_count = 0
        self._arena = np.empty(1024, GLYPH)
        self._spans = np.zeros((256, 2), np.intp)
        self._floats = np.zeros((256, 5), np.float32)

    def _build(self, text: str, setup: _FontSetup) -> tuple[NDArray[np.void], float, NDArray[np.float32]]:
        """Lay out *text* one QTextLayout per line and collect snapped glyph rectangles."""
        dpr = self._dpr
        spacing = setup.line_height * dpr
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        rows: list[tuple[int, int, int, int, int, int]] = []
        width = 0.0
        for index, line_text in enumerate(LINE_BREAKS.split(text)):
            layout = QTextLayout(line_text, setup.font, setup.device)
            layout.setTextOption(option)
            layout.beginLayout()
            line = layout.createLine()
            if line.isValid():
                line.setNumColumns(len(line_text.encode("utf-16-le")) // 2)
                width = max(width, line.horizontalAdvance())
                line.setPosition(QPointF(0, 0))
            layout.endLayout()
            if not line.isValid():
                continue
            baseline = round(index * spacing)
            for run in layout.glyphRuns():
                raw = QRawFont(run.rawFont())  # The same face at device size, for the bitmaps.
                raw.setPixelSize(raw.pixelSize() * dpr)
                for glyph, position in zip(run.glyphIndexes(), run.positions()):
                    slot = self._slot(raw, glyph)
                    if slot is not None:
                        dx = round(position.x() * dpr) + slot.left
                        dy = baseline + round((position.y() - line.ascent()) * dpr) + slot.top
                        rows.append((dx, dy, slot.w, slot.h, slot.u, slot.v))
        glyphs = np.array(rows, GLYPH)
        if len(glyphs):
            x, y = glyphs["dx"].astype(np.int64), glyphs["dy"].astype(np.int64)
            box = np.array([np.min(x), np.min(y), np.max(x + glyphs["w"]), np.max(y + glyphs["h"])], np.float32)
            ink = box / np.float32(dpr)
        else:
            ink = _NO_INK
        return glyphs, width, ink

    def _slot(self, raw: QRawFont, glyph: int) -> _Slot | None:
        """Return the glyph's page rectangle, drawing it into the atlas on first use."""
        key = (raw.familyName(), raw.styleName(), raw.weight(), round(raw.pixelSize(), 3), glyph)
        if key in self._slots:
            return self._slots[key]
        if raw.pixelSize() > PAGE_SIZE:  # No glyph of this font can fit the page; do not rasterize it.
            _warn_oversized_font(raw.pixelSize())
            self._slots[key] = None
            return None
        image = raw.alphaMapForGlyph(glyph, QRawFont.AntialiasingType.PixelAntialiasing, QTransform())
        slot: _Slot | None = None
        if not image.isNull() and 0 < image.width() <= PAGE_SIZE and 0 < image.height() <= PAGE_SIZE:
            if image.format() != QImage.Format.Format_Alpha8:
                image = image.convertToFormat(QImage.Format.Format_Alpha8)
            box = raw.boundingRect(glyph)
            try:
                u, v = self._allocate(image.width(), image.height())
            except AtlasFull:
                if not self.drop_overflow:
                    raise
                self.dropped += 1
                self._slots[key] = None
                return None
            painter = QPainter(self._page)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(u, v, image)
            painter.end()
            self._snapshot = None
            slot = _Slot(u, v, image.width(), image.height(), math.floor(box.x()), math.floor(box.y()))
        self._slots[key] = slot
        return slot

    def _allocate(self, w: int, h: int) -> tuple[int, int]:
        """Place a w by h rectangle on the page's shelf; raise AtlasFull when it does not fit."""
        x, y, row = self._shelf
        if x + w + 1 > PAGE_SIZE:
            x, y, row = 0, y + row + 1, 0
        if y + h + 1 > PAGE_SIZE:
            raise AtlasFull
        self._shelf = (x + w + 1, y, max(row, h))
        return x, y


_ATLASES: dict[tuple[int, float], GlyphAtlas] = {}


def glyph_atlas(dpi: int, dpr: float) -> GlyphAtlas:
    """Return the atlas shared by every lane drawing at this dpi and device pixel ratio."""
    atlas = _ATLASES.get((dpi, dpr))
    if atlas is None:
        atlas = _ATLASES[(dpi, dpr)] = GlyphAtlas(dpi, dpr)
    return atlas
