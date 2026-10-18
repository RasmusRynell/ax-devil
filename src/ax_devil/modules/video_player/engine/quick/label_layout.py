"""Label layout on the glyph atlas: a batch of labels becomes overlay quads without a per-label Python loop.

A label is a background (rounded rectangle), bars and glyphs, in that order. Run advances come from the shaped text
widths in logical pixels, so layout does not drift at device pixel ratios above one. Labels land on whole device
pixels and so do their glyph origins, which keeps the atlas bitmaps crisp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from numpy.typing import NDArray

from ..drawing import ANCHORS, LabelColumns, Paint, RunColumns
from . import glyphs as atlas_module
from .glyphs import LINE_BREAKS, FontKey, GlyphAtlas, ShapedTexts
from .overlay_vertex import CORNER_U, CORNER_V, OVERLAY_VERTEX, PAD, OverlayKind, per_row, premultiplied

if TYPE_CHECKING:
    from .preparation import DrawingSettings

_BAR_LENGTH, _BAR_THICKNESS, _BAR_TRACK_ALPHA = 2.5, 0.3, 70
"""A label bar's length and thickness in font sizes, and the opacity of its unfilled track."""
_BACKGROUND, _BARS, _GLYPHS = 0, 1, 2


class GlyphQuads(NamedTuple):
    """Glyph quads, four vertices each. *owner* is the index into the rows given to ``text_quads``."""

    vertices: NDArray[np.void]
    owner: NDArray[np.intp]


class LabelQuads(NamedTuple):
    """Quads of labels (or texts) in drawing order and how many labels (texts) are shown."""

    vertices: NDArray[np.void]
    labels: int


def visible_rows(
    culling: tuple[float, float, float, float] | None,
    dpr: float,
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    width: NDArray[np.float64],
    height: NDArray[np.float64],
    stroke: NDArray[np.float64],
) -> NDArray[np.bool_]:
    """Rows whose bounds, grown by half their stroke and a device pixel, touch the culling area (all without one)."""
    if culling is None:
        return np.ones(len(x), dtype=np.bool_)
    left, top, w, h = culling
    margin = np.maximum(0.0, stroke) * 0.7071067811865476 + 2 / dpr
    return (x - margin < left + w) & (x + width + margin > left) & (y - margin < top + h) & (y + height + margin > top)


def text_quads(
    glyphs: NDArray[np.void],
    start: NDArray[np.intp],
    count: NDArray[np.intp],
    rows: NDArray[np.intp],
    origin_x: NDArray[np.int64],
    origin_y: NDArray[np.int64],
    fill: NDArray[np.uint8],
    dpr: float,
) -> GlyphQuads:
    """Glyph quads of the *rows* of shaped texts at ``glyphs[start:start + count]``.

    Origins are baseline origins in device pixels and *fill* is premultiplied, both one per row of *rows*.
    """
    counts = count[rows]
    ends = np.cumsum(counts)
    owner = np.repeat(np.arange(len(rows)), counts)
    glyph = glyphs[np.arange(int(ends[-1]) if len(ends) else 0) + np.repeat(start[rows] - ends + counts, counts)]
    # Float32 throughout: half the memory traffic, and exact for device pixels and texels at these magnitudes.
    inverse_dpr, inverse_page = np.float32(1 / dpr), np.float32(1 / atlas_module.PAGE_SIZE)
    w, h = glyph["w"].astype(np.float32), glyph["h"].astype(np.float32)
    left = (origin_x[owner] + glyph["dx"]).astype(np.float32) * inverse_dpr
    top = (origin_y[owner] + glyph["dy"]).astype(np.float32) * inverse_dpr
    u, v = glyph["u"].astype(np.float32) * inverse_page, glyph["v"].astype(np.float32) * inverse_page
    out = np.zeros((len(owner), 4), OVERLAY_VERTEX)
    # Corners run top-left, top-right, bottom-left, bottom-right.
    right, bottom = left + w * inverse_dpr, top + h * inverse_dpr
    out["x"][:, 0::2], out["x"][:, 1::2] = left[:, None], right[:, None]
    out["y"][:, :2], out["y"][:, 2:] = top[:, None], bottom[:, None]
    u_right, v_bottom = u + w * inverse_page, v + h * inverse_page
    out["u"][:, 0::2], out["u"][:, 1::2] = u[:, None], u_right[:, None]
    out["v"][:, :2], out["v"][:, 2:] = v[:, None], v_bottom[:, None]
    out["kind"] = float(OverlayKind.GLYPH)
    _set_color(out["fill"], _pack(fill)[owner][:, None])
    return GlyphQuads(out, owner)


def text_primitive_quads(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    text: NDArray[np.object_],
    anchor: NDArray[np.object_],
    paint: Paint,
    settings: DrawingSettings,
    atlas: GlyphAtlas,
) -> LabelQuads:
    """Glyph quads of the text primitive at normalized *x*, *y*; ``baseline`` anchors put the baseline at the point.

    Other anchors place the text's line box (font ascent and descent plus any further lines) by its fraction of the
    box. Text lands on whole device pixels, in fonts snapped to half device pixels so that sizes following object
    sizes share a few fonts, and rows outside the culling rectangle produce nothing.
    """
    count, dpr = len(x), settings.dpr
    pixels = np.rint(per_row(paint.font_size, count, np.float64) * settings.scale * dpr * 2) / 2
    ok = np.isfinite(pixels) & (pixels > 0) & np.isfinite(x) & np.isfinite(y)
    rgb = np.column_stack([per_row(channel, count, np.float64) for channel in (paint.pen_r, paint.pen_g, paint.pen_b)])
    empty = LabelQuads(np.zeros((0, 4), OVERLAY_VERTEX), 0)
    if not ok.any():
        return empty
    width, ink = np.zeros(count), np.zeros((count, 4))
    ascent, descent, line_height = np.zeros(count), np.zeros(count), np.zeros(count)
    shaped: list[tuple[NDArray[np.intp], ShapedTexts]] = []
    for group in _groups((paint.font_family, pixels), np.flatnonzero(ok)):
        first = int(group[0])
        key = FontKey(str(_value(paint.font_family, first)), float(pixels[first]), "regular")
        font = atlas.shape(text[group], key)
        width[group], ink[group] = font.width, font.ink
        ascent[group], descent[group], line_height[group] = font.ascent, font.descent, font.line_height
        shaped.append((group, font))
    rows = np.flatnonzero(ok)
    lines = np.zeros(count, np.intp)
    lines[rows] = np.fromiter((len(LINE_BREAKS.findall(item)) for item in text[rows].tolist()), np.intp, len(rows))
    height = ascent + descent + lines * line_height
    factors = _anchor_factors(anchor, count)
    left = x * settings.width - factors[:, 0] * width
    base = y * settings.height + np.where(anchor == "baseline", 0.0, ascent - factors[:, 1] * height)
    seen = visible_rows(
        settings.culling(),
        dpr,
        left + ink[:, 0],
        base + ink[:, 1],
        ink[:, 2] - ink[:, 0],
        ink[:, 3] - ink[:, 1],
        np.zeros(count),
    )
    shown = seen & ok
    batches = []
    for group, font in shaped:
        keep = np.flatnonzero(shown[group])
        rows = group[keep]
        batches.append(
            text_quads(
                font.glyphs,
                font.start,
                font.count,
                keep,
                np.rint(left[rows] * dpr).astype(np.int64),
                np.rint(base[rows] * dpr).astype(np.int64),
                premultiplied(rgb[rows], 255),
                dpr,
            ).vertices
        )
    if not shown.any():
        return empty
    return LabelQuads(np.concatenate(batches), int(np.count_nonzero(shown)))


def _pack(rgba: NDArray[np.uint8]) -> NDArray[np.uint32]:
    """One 32-bit word per RGBA row, so a colour is written with a single store."""
    packed: NDArray[np.uint32] = np.ascontiguousarray(rgba).view(np.uint32)[:, 0]
    return packed


def _set_color(field: NDArray[np.uint8], packed: NDArray[np.uint32]) -> None:
    """Assign packed colours to an RGBA vertex field."""
    field.view(np.uint32)[..., 0] = packed


def _rect_quads(
    left: NDArray[np.float64],
    top: NDArray[np.float64],
    width: NDArray[np.float64],
    height: NDArray[np.float64],
    radius: NDArray[np.float64],
    fill: NDArray[np.uint8],
) -> NDArray[np.void]:
    """Filled rounded rectangles, padded for the shader's antialiasing."""
    u = -PAD + (width[:, None] + 2 * PAD) * CORNER_U
    v = -PAD + (height[:, None] + 2 * PAD) * CORNER_V
    out = np.zeros((len(left), 4), OVERLAY_VERTEX)
    out["x"], out["y"] = left[:, None] + u, top[:, None] + v
    out["u"], out["v"] = u, v
    out["w"], out["h"], out["radius"] = width[:, None], height[:, None], radius[:, None]
    out["kind"] = float(OverlayKind.ROUNDED_RECT)
    _set_color(out["fill"], _pack(fill)[:, None])
    return out


def _value(column: object, row: int) -> object:
    return column[row] if type(column) is np.ndarray and column.ndim else column


def _groups(columns: tuple[object, ...], rows: NDArray[np.intp]) -> list[NDArray[np.intp]]:
    """Split *rows* into groups that agree on every per-row column; shared columns agree by definition."""
    if not rows.size:
        return []
    codes = np.zeros(len(rows), np.intp)
    for column in columns:
        if type(column) is np.ndarray and column.ndim:
            _, inverse = np.unique(column[rows], return_inverse=True)
            inverse = inverse.reshape(-1)
            codes = codes * (int(np.max(inverse)) + 1) + inverse
    if not codes.any():
        return [rows]
    unique, inverse = np.unique(codes, return_inverse=True)
    inverse = inverse.reshape(-1)
    return [rows[inverse == group] for group in range(len(unique))]


class _AnchorCodes(dict[str, int]):
    """Codes of the anchor names; any other name gets the code of ``(0, 0)``."""

    def __missing__(self, name: str) -> int:
        return len(ANCHORS)


_ANCHOR_CODES = _AnchorCodes({name: code for code, name in enumerate(ANCHORS)})
_ANCHOR_FACTORS = np.array([*ANCHORS.values(), (0.0, 0.0)])
"""Factors by anchor code; the last row serves names that are no anchor."""


def _anchor_factors(anchor: object, count: int) -> NDArray[np.float64]:
    """Each row's (horizontal, vertical) fraction of its label's size to move up and left."""
    if type(anchor) is np.ndarray and anchor.ndim:
        return _ANCHOR_FACTORS[np.fromiter(map(_ANCHOR_CODES.__getitem__, anchor.tolist()), np.intp, count)]
    return np.broadcast_to(_ANCHOR_FACTORS[_ANCHOR_CODES[str(anchor)]], (count, 2))


@dataclass
class _Run:
    """One run position of every row: what is shown, how far it advances and the shaped rows that draw it."""

    columns: RunColumns
    shown: NDArray[np.bool_]
    is_bar: NDArray[np.bool_]
    advance: NDArray[np.float64]
    ink: NDArray[np.float64]
    ascent: NDArray[np.float64]
    descent: NDArray[np.float64]
    cap_height: NDArray[np.float64]
    shaped: list[tuple[NDArray[np.intp], ShapedTexts]] = field(default_factory=list)
    offset: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))


def _shape_run(
    columns: RunColumns,
    labels: LabelColumns,
    ok: NDArray[np.bool_],
    size: NDArray[np.float64],
    dpr: float,
    atlas: GlyphAtlas,
) -> _Run:
    count = len(ok)
    text = per_row(columns.text, count, object)
    bar = per_row(columns.bar, count, np.float64)
    is_bar = ~np.isnan(bar)
    shown = ok & per_row(columns.present, count, np.bool_) & ((text != "") | is_bar)
    run = _Run(
        columns,
        shown,
        is_bar & shown,
        np.zeros(count),
        np.zeros((count, 4)),
        np.zeros(count),
        np.zeros(count),
        np.zeros(count),
    )
    for group in _groups((labels.family, labels.size, columns.weight), np.flatnonzero(shown)):
        first = int(group[0])
        key = FontKey(str(_value(labels.family, first)), float(size[first] * dpr), str(_value(columns.weight, first)))  # type: ignore[arg-type]
        bars = is_bar[group]
        texts = text[group]
        shaped = atlas.shape(np.where(bars, "", texts) if bars.any() else texts, key)
        run.advance[group] = np.where(bars, size[group] * _BAR_LENGTH, shaped.width)
        run.ink[group] = shaped.ink
        run.ascent[group], run.descent[group] = shaped.ascent, shaped.descent
        if bars.any():
            run.cap_height[group] = -float(atlas.shape(["H"], key).ink[0, 1])
        run.shaped.append((group, shaped))
    return run


def label_quads(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    anchor: object,
    labels: LabelColumns,
    settings: DrawingSettings,
    atlas: GlyphAtlas,
) -> LabelQuads:
    """Quads for normalized label anchors *x*, *y*: background, bars, then glyphs, label after label.

    Lengths scale like fonts do. A label is as large as its runs' ink and its padding, rounded up to device pixels, and
    sits with its *anchor* at the point. Rows without a shown run, or outside the culling area, produce nothing.
    """
    count, dpr, scale = len(x), settings.dpr, settings.scale
    size = per_row(labels.size, count, np.float64) * scale
    ok = np.isfinite(size) & (size > 0)
    pad_x, pad_y, gap, radius = (
        per_row(column, count, np.float64) * scale
        for column in (labels.padding_x, labels.padding_y, labels.gap, labels.radius)
    )
    runs = [_shape_run(columns, labels, ok, size, dpr, atlas) for columns in labels.runs]

    running = np.zeros(count)
    ascent, descent = np.zeros(count), np.zeros(count)
    for run in runs:
        run.offset = running
        running = running + np.where(run.shown, run.advance + gap, 0.0)
        ascent, descent = np.maximum(ascent, run.ascent), np.maximum(descent, run.descent)
    total = running - gap
    left, right, top, bottom = np.zeros(count), total, -ascent, descent
    for run in runs:
        left = np.minimum(left, np.where(run.shown, run.offset + run.ink[:, 0], 0.0))
        right = np.maximum(right, np.where(run.shown, run.offset + run.ink[:, 2], 0.0))
        top = np.minimum(top, np.where(run.shown, run.ink[:, 1], 0.0))
        bottom = np.maximum(bottom, np.where(run.shown, run.ink[:, 3], 0.0))
    any_shown = np.any([run.shown for run in runs], axis=0) if runs else np.zeros(count, np.bool_)
    # Device pixels the label covers, rounded up; a hair of tolerance keeps float noise from adding a pixel.
    width = np.ceil((right - left + 2 * pad_x) * dpr - 1e-3) / dpr
    height = np.ceil((bottom - top + 2 * pad_y) * dpr - 1e-3) / dpr
    factors = _anchor_factors(anchor, count)
    exact_x = x * settings.width - factors[:, 0] * width
    exact_y = y * settings.height - factors[:, 1] * height
    drawn = (
        any_shown
        & np.isfinite(exact_x)
        & np.isfinite(exact_y)
        & (0 < width)
        & (width * dpr < 2**24)
        & (0 < height)
        & (height * dpr < 2**24)
    )
    drawn &= visible_rows(settings.culling(), dpr, exact_x, exact_y, width, height, np.zeros(count))
    placed_x, placed_y = np.rint(exact_x * dpr) / dpr, np.rint(exact_y * dpr) / dpr
    inner_x, baseline = placed_x + pad_x - left, placed_y + pad_y - top

    parts: list[tuple[int, NDArray[np.void], NDArray[np.intp]]] = []
    # (order within the label, quads, label of each quad in ascending order)

    def add(quads: NDArray[np.void], rows: NDArray[np.intp], part: int, sub: int) -> None:
        if len(quads):
            parts.append((part * 16 + sub, quads, rows))

    if labels.background is not None:
        red, green, blue, alpha = (per_row(channel, count, np.float64) for channel in labels.background)
        rows = np.flatnonzero(drawn & (alpha > 0))
        fill = premultiplied(np.column_stack((red, green, blue))[rows], alpha[rows])
        rounded = np.minimum(radius, np.minimum(width, height) / 2)[rows]
        add(
            _rect_quads(placed_x[rows], placed_y[rows], width[rows], height[rows], rounded, fill),
            rows,
            _BACKGROUND,
            0,
        )
    for index, run in enumerate(runs):
        color = np.column_stack(
            [per_row(channel, count, np.float64) for channel in (run.columns.red, run.columns.green, run.columns.blue)]
        )
        rows = np.flatnonzero(drawn & run.is_bar)
        if rows.size:
            thickness = np.maximum(2.0, size[rows] * _BAR_THICKNESS)
            bar_left, length = (inner_x + run.offset)[rows], run.advance[rows]
            bar_top = baseline[rows] - run.cap_height[rows] / 2 - thickness / 2
            filled = np.clip(per_row(run.columns.bar, count, np.float64)[rows], 0.0, 1.0)
            track = premultiplied(color[rows], _BAR_TRACK_ALPHA)
            add(_rect_quads(bar_left, bar_top, length, thickness, thickness / 2, track), rows, _BARS, 2 * index)
            some = filled > 0
            add(
                _rect_quads(
                    bar_left[some],
                    bar_top[some],
                    np.maximum(thickness, length * filled)[some],
                    thickness[some],
                    thickness[some] / 2,
                    premultiplied(color[rows], 255)[some],
                ),
                rows[some],
                _BARS,
                2 * index + 1,
            )
        for group, shaped in run.shaped:
            keep = np.flatnonzero(drawn[group])
            rows = group[keep]
            glyphs = text_quads(
                shaped.glyphs,
                shaped.start,
                shaped.count,
                keep,
                np.rint((inner_x + run.offset)[rows] * dpr).astype(np.int64),
                np.rint(baseline[rows] * dpr).astype(np.int64),
                premultiplied(color[rows], 255),
                dpr,
            )
            add(glyphs.vertices, rows[glyphs.owner], _GLYPHS, index)

    shown = int(np.count_nonzero(drawn))
    if not parts:
        return LabelQuads(np.zeros((0, 4), OVERLAY_VERTEX), shown)
    # Scatter every part into its labels' blocks: a label's quads are ordered by part, and a part's quads by label.
    parts.sort(key=lambda part: part[0])
    per_part = [np.bincount(rows, minlength=count) for _, _, rows in parts]
    cursor = np.cumsum(sum(per_part)) - sum(per_part)
    vertices = np.empty((sum(len(quads) for _, quads, _ in parts), 4), OVERLAY_VERTEX)
    for (_, quads, rows), quads_per_label in zip(parts, per_part):
        slot = cursor[rows] + np.arange(len(rows)) - (np.cumsum(quads_per_label) - quads_per_label)[rows]
        cursor = cursor + quads_per_label
        vertices[slot] = quads
    return LabelQuads(vertices, shown)
