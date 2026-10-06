"""Drawing target contract and shared style/text preparation resources.

All coordinates in normalized [0,1] space relative to video frame.
Uses bounded caches for Qt resources and shaped text. Subpixel geometry is preserved.

Supports text anchoring system for precise text positioning:
- baseline: Original Qt baseline positioning
- top-left, top-center, top-right: Text positioned relative to top edge
- center, center-right: Text positioned relative to center
- bottom-left, bottom-center, bottom-right: Text positioned relative to bottom edge
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from math import ceil
from typing import Literal, Protocol, TypeAlias, cast
from weakref import WeakValueDictionary

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QTextLayout,
    QTextOption,
)

PenStyle: TypeAlias = Literal["solid", "dash"]


@lru_cache(maxsize=512)
def _get_font(family: str, size_px: float, dpi: int) -> QFont:
    """Resolve an exact logical pixel size using Qt's fractional point-size API."""
    font = QFont(family)
    font.setPointSizeF(size_px * 72.0 / dpi)
    return font


@dataclass(frozen=True)
class _TextBlock:
    __slots__ = ("device", "layouts", "width", "height", "ascent", "__weakref__")

    device: QImage
    layouts: tuple[QTextLayout, ...]
    width: float
    height: float
    ascent: float


_LIVE_TEXT_BLOCKS: WeakValueDictionary[tuple[str, str, float, int], _TextBlock] = WeakValueDictionary()


_LINE_BREAKS = re.compile(r"\r\n|[\r\n\u2028\u2029]")
_ANCHORS: dict[str, tuple[float, float]] = {
    "top-left": (0, 0),
    "top-center": (0.5, 0),
    "top-right": (1, 0),
    "center-left": (0, 0.5),
    "center": (0.5, 0.5),
    "center-right": (1, 0.5),
    "bottom-left": (0, 1),
    "bottom-center": (0.5, 1),
    "bottom-right": (1, 1),
    "top": (0.5, 0),
    "bottom": (0.5, 1),
    "left": (0, 0.5),
    "right": (1, 0.5),
}


@lru_cache(maxsize=512)
def _text_metrics(family: str, size: float, dpi: int) -> tuple[QImage, QFont, float, float, float]:
    # Immutable font metrics and their device are shared across changing strings.
    # The device stays independent of widget/export lifetimes and is never painted.
    device = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    device.setDotsPerMeterX(round(dpi / 0.0254))
    device.setDotsPerMeterY(round(dpi / 0.0254))
    font = _get_font(family, size, dpi)
    metrics = QFontMetricsF(font, device)
    return device, font, metrics.ascent(), metrics.descent(), metrics.lineSpacing()


@lru_cache(maxsize=1024)
def _text_block(text: str, family: str, size: float, dpi: int) -> _TextBlock:
    key = (text, family, size, dpi)
    retained = _LIVE_TEXT_BLOCKS.get(key)
    if retained is not None:
        return retained
    device, font, ascent, descent, spacing = _text_metrics(family, size, dpi)
    layouts: list[QTextLayout] = []
    width = 0.0
    for text_line in _LINE_BREAKS.split(text):
        layout = QTextLayout(text_line, font, device)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        layout.setTextOption(option)
        layout.setCacheEnabled(True)
        layout.beginLayout()
        line = layout.createLine()
        if line.isValid():
            line.setNumColumns(len(text_line.encode("utf-16-le")) // 2)
            width = max(width, line.horizontalAdvance())
            ascent = max(ascent, line.ascent())
            descent = max(descent, line.descent())
            spacing = max(spacing, line.height() + line.leading())
        layout.endLayout()
        layouts.append(layout)
    for index, layout in enumerate(layouts):
        if layout.lineCount():
            line = layout.lineAt(0)
            line.setPosition(QPointF(0, index * spacing + ascent - line.ascent()))
    block = _TextBlock(device, tuple(layouts), width, ascent + descent + (len(layouts) - 1) * spacing, ascent)
    _LIVE_TEXT_BLOCKS[key] = block
    return block


@lru_cache(maxsize=1024)
def text_ink_bounds(text: str, family: str, size: float, dpi: int) -> QRectF:
    """Resolve actual glyph extents only when layout bounds alone would reject text."""
    bounds = QRectF()
    for layout in _text_block(text, family, size, dpi).layouts:
        for run in layout.glyphRuns():
            # Discard the layout's selection bounds and request complete glyph ink.
            run.setBoundingRect(QRectF())
            bounds = bounds.united(run.boundingRect().translated(layout.position()))
    return bounds


LabelWeight: TypeAlias = Literal["regular", "medium", "semibold", "bold"]
_WEIGHTS: dict[str, QFont.Weight] = {
    "regular": QFont.Weight.Normal,
    "medium": QFont.Weight.Medium,
    "semibold": QFont.Weight.DemiBold,
    "bold": QFont.Weight.Bold,
}


@dataclass(frozen=True, slots=True)
class LabelRun:
    """One stretch of a label: text in one color and weight, or, when *bar* is set, a bar that much filled.

    A bar is as long as two and a half font sizes; its unfilled part is a faint track in the same color.
    """

    text: str
    color: tuple[int, int, int]
    weight: LabelWeight = "regular"
    bar: float | None = None


@dataclass(frozen=True, slots=True)
class LabelContent:
    """Everything a label shows, independent of where: equal content draws identical pixels.

    Lengths are relative to min(width,height), like font sizes in ``Paint``. An empty family uses the
    application's default font. Runs are laid out left to right on one line, *gap* apart.
    """

    runs: tuple[LabelRun, ...]
    size: float
    family: str = ""
    background: tuple[int, int, int, int] | None = None
    padding_x: float = 0.0
    padding_y: float = 0.0
    radius: float = 0.0
    gap: float = 0.0


@dataclass(frozen=True, eq=False)
class LabelSprite:
    """A label drawn once at device resolution; *width* and *height* are logical pixels."""

    __slots__ = ("image", "width", "height", "__weakref__")

    image: QImage
    width: float
    height: float


_LIVE_LABEL_SPRITES: WeakValueDictionary[tuple[LabelContent, float, int, float], LabelSprite] = WeakValueDictionary()


def _label_font(family: str, size: float, weight: LabelWeight, dpi: int) -> QFont:
    font = QFont(_get_font(family, size, dpi)) if family else QFont()
    if not family:
        font.setPointSizeF(size * 72.0 / dpi)
    font.setWeight(_WEIGHTS[weight])
    return font


_BAR_LENGTH, _BAR_THICKNESS, _BAR_TRACK_ALPHA = 2.5, 0.3, 70
"""A label bar's length and thickness in font sizes, and the opacity of its unfilled track."""


def _paint_bar(painter: QPainter, line: QRectF, run: LabelRun, size: float) -> None:
    """Paint a rounded bar along *line*, a zero-height rectangle on the bar's center line."""
    thickness = max(2.0, size * _BAR_THICKNESS)
    track = QRectF(line.left(), line.top() - thickness / 2, line.width(), thickness)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(*run.color, _BAR_TRACK_ALPHA))
    painter.drawRoundedRect(track, thickness / 2, thickness / 2)
    if run.bar:
        painter.setBrush(QColor(*run.color))
        filled = QRectF(track.left(), track.top(), max(thickness, track.width() * run.bar), thickness)
        painter.drawRoundedRect(filled, thickness / 2, thickness / 2)


@lru_cache(maxsize=1024)
def label_sprite(content: LabelContent, scale: float, dpi: int, dpr: float) -> LabelSprite | None:
    """Paint a label sized to its runs, or return None when its raster cannot be represented or allocated."""
    key = (content, scale, dpi, dpr)
    retained = _LIVE_LABEL_SPRITES.get(key)
    if retained is not None:
        return retained
    device = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    device.setDotsPerMeterX(round(dpi / 0.0254))
    device.setDotsPerMeterY(round(dpi / 0.0254))
    size = content.size * scale
    if not 0 < size * dpr < 2**31:
        return None
    fonts = [_label_font(content.family, size, run.weight, dpi) for run in content.runs]
    metrics = [QFontMetricsF(font, device) for font in fonts]
    advances = [
        size * _BAR_LENGTH if run.bar is not None else metric.horizontalAdvance(run.text)
        for metric, run in zip(metrics, content.runs)
    ]
    ascent = max(metric.ascent() for metric in metrics)
    descent = max(metric.descent() for metric in metrics)
    pad_x, pad_y, gap = content.padding_x * scale, content.padding_y * scale, content.gap * scale
    bounds = QRectF(0, -ascent, sum(advances) + gap * (len(advances) - 1), ascent + descent)
    x = 0.0
    for run, metric, advance in zip(content.runs, metrics, advances):
        if run.bar is None:
            bounds = bounds.united(metric.tightBoundingRect(run.text).translated(x, 0))
        x += advance + gap
    width = bounds.width() + 2 * pad_x
    height = bounds.height() + 2 * pad_y
    # QImage dimensions are signed C++ ints; reject overflow before ceil() or the binding can raise.
    if not (0 < width * dpr <= 2**31 - 1 and 0 < height * dpr <= 2**31 - 1):
        return None
    image = QImage(max(1, ceil(width * dpr)), max(1, ceil(height * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        return None
    width, height = image.width() / dpr, image.height() / dpr
    image.setDotsPerMeterX(device.dotsPerMeterX())
    image.setDotsPerMeterY(device.dotsPerMeterY())
    image.setDevicePixelRatio(dpr)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    if content.background is not None:
        radius = min(content.radius * scale, height / 2, width / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*content.background))
        painter.drawRoundedRect(QRectF(0, 0, width, height), radius, radius)
    x, baseline = pad_x - bounds.left(), pad_y - bounds.top()
    for run, font, metric, advance in zip(content.runs, fonts, metrics, advances):
        if run.bar is not None:
            _paint_bar(painter, QRectF(x, baseline - metric.capHeight() / 2, advance, 0), run, size)
        else:
            painter.setFont(font)
            painter.setPen(QColor(*run.color))
            painter.drawText(QPointF(x, baseline), run.text)
        x += advance + gap
    painter.end()
    sprite = LabelSprite(image, width, height)
    _LIVE_LABEL_SPRITES[key] = sprite
    return sprite


@dataclass(frozen=True, slots=True)
class DrawingStyle:
    """Immutable drawing style; lengths are relative to min(width,height).

    Colors: RGB [0,255], Alpha [0,255] where 0=transparent, 255=opaque
    Sizes: Relative to video dimensions [0,1]
    """

    pen_r: int = 255
    pen_g: int = 0
    pen_b: int = 0
    pen_width: float = 0.002  # 0.2% of min dimension
    pen_style: PenStyle = "solid"
    brush_r: int = 0
    brush_g: int = 0
    brush_b: int = 0
    brush_a: int = 0  # 0 = no fill
    font_family: str = "Arial"
    font_size: float = 0.02  # 2% of min dimension
    radius: float = 0.0  # Corner radius of boxes; other shapes ignore it


Points: TypeAlias = tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class Paint:
    """Drawing style for a batch of rows.

    Each field is either one value shared by every row or an array with one value per row.
    Units follow DrawingStyle: colors in [0,255], widths and font sizes relative to min(width,height).
    """

    pen_r: object = 255
    pen_g: object = 0
    pen_b: object = 0
    pen_width: object = 0.002
    dashed: object = False
    brush_r: object = 0
    brush_g: object = 0
    brush_b: object = 0
    brush_a: object = 0
    font_family: object = "Arial"
    font_size: object = 0.02
    radius: object = 0.0

    @classmethod
    def of(cls, style: DrawingStyle) -> Paint:
        """Share one style between every row of a batch."""
        return cls(
            style.pen_r,
            style.pen_g,
            style.pen_b,
            style.pen_width,
            style.pen_style == "dash",
            style.brush_r,
            style.brush_g,
            style.brush_b,
            style.brush_a,
            style.font_family,
            style.font_size,
            style.radius,
        )

    @classmethod
    def concatenate(cls, parts: Sequence[tuple[int, Paint]]) -> Paint:
        """Join batches' rows in order; values shared by every part stay shared."""
        fields = []
        for name in _PAINT_FIELDS:
            values = [getattr(paint, name) for _, paint in parts]
            first = values[0]
            if all(type(value) is not np.ndarray and value == first for value in values):
                fields.append(first)
            else:
                fields.append(
                    np.concatenate(
                        [np.broadcast_to(np.asarray(value), (count,)) for (count, _), value in zip(parts, values)]
                    )
                )
        return cls(*fields)

    def take(self, rows: NDArray[np.intp]) -> Paint:
        """Select per-row values for the given rows; shared values stay shared."""
        return Paint(*(_take(getattr(self, name), rows) for name in _PAINT_FIELDS))

    def value(self, name: str, row: int) -> object:
        """Return one row's value of a field."""
        value = getattr(self, name)
        return value[row] if type(value) is np.ndarray else value

    def style(self, row: int) -> DrawingStyle:
        """Return one row as an immutable scalar style."""
        return _style(*(_scalar(name, self.value(name, row)) for name in _PAINT_FIELDS))


_PAINT_FIELDS = tuple(Paint.__dataclass_fields__)
_INTEGER_FIELDS = frozenset({"pen_r", "pen_g", "pen_b", "brush_r", "brush_g", "brush_b", "brush_a"})


def _take(value: object, rows: NDArray[np.intp]) -> object:
    return value[rows] if type(value) is np.ndarray else value


def _scalar(name: str, value: object) -> object:
    if name in _INTEGER_FIELDS:
        return int(cast(float, value))
    if name == "dashed":
        return bool(value)
    if name == "font_family":
        return str(value)
    return float(cast(float, value))


@lru_cache(maxsize=1024)
def _style(
    pen_r: int,
    pen_g: int,
    pen_b: int,
    pen_width: float,
    dashed: bool,
    brush_r: int,
    brush_g: int,
    brush_b: int,
    brush_a: int,
    font_family: str,
    font_size: float,
    radius: float,
) -> DrawingStyle:
    return DrawingStyle(
        pen_r,
        pen_g,
        pen_b,
        pen_width,
        "dash" if dashed else "solid",
        brush_r,
        brush_g,
        brush_b,
        brush_a,
        font_family,
        font_size,
        radius,
    )


class DrawingTarget(Protocol):
    """Consume batches of drawing instructions; row i of every argument describes one primitive.

    Coordinates are normalized to the target image. Numeric arguments are float arrays of equal length.
    """

    def boxes(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        w: NDArray[np.float64],
        h: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare normalized rectangles; zero-area rows draw nothing."""

    def circles(
        self, x: NDArray[np.float64], y: NDArray[np.float64], radius: NDArray[np.float64], paint: Paint
    ) -> None:
        """Prepare circles with radius relative to the smaller target dimension."""

    def lines(
        self,
        x1: NDArray[np.float64],
        y1: NDArray[np.float64],
        x2: NDArray[np.float64],
        y2: NDArray[np.float64],
        paint: Paint,
    ) -> None:
        """Prepare square-capped lines, including coincident endpoints."""

    def points(self, x: NDArray[np.float64], y: NDArray[np.float64], paint: Paint) -> None:
        """Prepare square points following the stroke width."""

    def polygons(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Prepare closed odd-even polygons; rows with equal corner counts may come as one (rows, corners, 2) array."""

    def polylines(self, points: Sequence[Points] | NDArray[np.float64], paint: Paint) -> None:
        """Prepare open polylines with bevel joins, given like polygon points."""

    def texts(
        self, x: NDArray[np.float64], y: NDArray[np.float64], text: Sequence[str], anchor: Sequence[str], paint: Paint
    ) -> None:
        """Prepare anchored, shaped text."""

    def labels(
        self,
        x: NDArray[np.float64],
        y: NDArray[np.float64],
        anchor: Sequence[str],
        content: Sequence[LabelContent],
    ) -> None:
        """Prepare labels; *anchor* places each label's background, sized to its runs, at its point."""


@lru_cache(maxsize=1024)
def _rectangle_path(width: float, height: float) -> str:
    return f"m 0 0 h {width:.12g} v {height:.12g} h {-width:.12g} z"


@lru_cache(maxsize=1024)
def _rounded_rectangle_path(width: float, height: float, radius: float) -> str:
    r = min(radius, width / 2, height / 2)
    arc = f"a {r:.12g} {r:.12g} 0 0 1"
    return (
        f"m {r:.12g} 0 h {width - 2 * r:.12g} {arc} {r:.12g} {r:.12g} v {height - 2 * r:.12g} "
        f"{arc} {-r:.12g} {r:.12g} h {2 * r - width:.12g} {arc} {-r:.12g} {-r:.12g} v {2 * r - height:.12g} "
        f"{arc} {r:.12g} {-r:.12g} z"
    )


@lru_cache(maxsize=512)
def _circle_path(radius: float) -> str:
    return (
        f"m {radius:.12g} 0 a {radius:.12g} {radius:.12g} 0 1 0 {-2 * radius:.12g} 0 "
        f"a {radius:.12g} {radius:.12g} 0 1 0 {2 * radius:.12g} 0 z"
    )


def _poly_path(points: Points | NDArray[np.float64], width: float, height: float) -> str:
    segments = ["m 0 0"]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        segments.append(f"l {(x1 - x0) * width:.12g} {(y1 - y0) * height:.12g}")
    return " ".join(segments)


def _poly_bounds(points: Points | NDArray[np.float64], width: float, height: float) -> QRectF:
    x, y = points[0]
    left = min(point[0] for point in points)
    top = min(point[1] for point in points)
    right = max(point[0] for point in points)
    bottom = max(point[1] for point in points)
    return QRectF((left - x) * width, (top - y) * height, (right - left) * width, (bottom - top) * height)
