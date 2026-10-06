"""Example sheets for the catalog viewer and ``ax-devil catalog render``, generated from the catalog being shown.

A sheet is one example frame: a Scene drawn by the catalog over footage-like background, with captions naming each
example. The **Overview** shows one object of every type and one pair of every relation. **Street** is a camera view of
a sunny street with people near and far, their heads, and traffic. Each object type then gets a sheet of the situations
real overlays produce (sizes, confidence levels, a long id, movement, outlines, overlaps and the frame edge) over
bright, busy, grey and dark ground, each relation a sheet of pairs at three sizes, and **Crowd** many objects at night.

Examples look like what decoders report: track ids in the usual formats, from small numbers to UUIDs and unique in
each sheet, and clothing and vehicle colors, flags and numbers drawn from plausible distributions. They are routed by
the catalog's own selectors and carry every detection attribute a type reads, so conditional parts are drawn.
The footage comes from ``footage.py`` and the street's people and traffic from ``street.py``.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from itertools import chain, count
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF, QImage

from ax_devil.modules.catalog_viewer.footage import (
    GRID_SIZE,
    day_street_background,
    empty_street_background,
    grid_background,
    night_street_background,
)
from ax_devil.modules.catalog_viewer.street import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    Actor,
    clipped,
    metre,
    street_actors,
)
from ax_devil.modules.scene.decoding.decoder_utils import rgb_from_map
from ax_devil.modules.scene.model import (
    Attribute,
    BoundingBox,
    Classification,
    ColorClassification,
    Entity,
    EntityId,
    EntityRelation,
    Geometry,
    ImageVelocity,
    MotionState,
    NormalizedPoint,
    Observation,
    Polygon,
    Scene,
    Score,
    TimeSlice,
)
from ax_devil.modules.scene.rendering import CachedSceneOverlay, SceneRenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.drawing import Paint
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext

OBJECT_GROUPS = ("classifications", "fallbacks")

_FALLBACK_NAMES = ("license_plate", "animal", "unknown", "example")
"""Class names a classified fallback example tries first; when types claim them all, ``example_2`` and on follow."""
_PART_OF = "has_part"
"""The relation from a person to their head in **Street**, drawn when the catalog has a recipe for it."""


@dataclass(frozen=True, slots=True)
class Caption:
    """Text naming the example below it, with an optional smaller second line.

    Position and width are normalized; text longer than the width is shortened with an ellipsis.
    """

    x: float
    y: float
    text: str
    detail: str = ""
    width: float = 0.25


@dataclass(frozen=True, slots=True)
class Sheet:
    """One example frame: its tab title, what it shows, the Scene, the captions and the footage it is drawn over."""

    key: str
    title: str
    description: str
    scene: Scene
    captions: tuple[Caption, ...]
    background: Callable[[], QImage]


@dataclass(frozen=True, slots=True)
class _Example:
    """One example object: where it is, how it looks to detection, and any id or attributes it must have."""

    box: tuple[float, float, float, float]
    score: float = 0.85
    velocity: tuple[float, float] | None = (0.06, -0.03)
    motion_state: MotionState | None = MotionState.Moving
    outline: bool = False
    id: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Recipe:
    """What a sheet needs to know about one object type or relation of the catalog."""

    group: str
    title: str
    subtitle: str
    data: Mapping[str, Any]

    @property
    def key(self) -> str:
        return str(self.data.get("id", self.title))

    @property
    def selector(self) -> Mapping[str, Any]:
        selector: Mapping[str, Any] = self.data.get("selector", {})
        return selector

    @property
    def types(self) -> list[str]:
        return [str(value) for value in self.selector.get("types", [])]


_LONG_ID = "3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f"
_CELL_W = _CELL_H = 1 / GRID_SIZE
_MEDIUM = (0.085, 0.07, 0.08, 0.14)
_SITUATIONS: tuple[tuple[str, str, tuple[_Example, ...]], ...] = (
    ("Tiny", "", (_Example((0.115, 0.1, 0.02, 0.04)),)),
    ("Small", "", (_Example((0.105, 0.09, 0.04, 0.08)),)),
    ("Medium", "", (_Example((0.09, 0.08, 0.07, 0.13)),)),
    ("Large", "", (_Example((0.07, 0.065, 0.11, 0.165)),)),
    ("Confidence 15%", "", (_Example(_MEDIUM, score=0.15),)),
    ("Confidence 50%", "", (_Example(_MEDIUM, score=0.5),)),
    ("Confidence 98%", "", (_Example(_MEDIUM, score=0.98),)),
    ("Long id", "36-character UUID", (_Example(_MEDIUM, score=0.72, id=_LONG_ID),)),
    ("Still", "", (_Example(_MEDIUM, velocity=None, motion_state=MotionState.Stationary),)),
    ("Moving slowly", "", (_Example(_MEDIUM, velocity=(0.03, -0.01)),)),
    ("Moving fast", "", (_Example(_MEDIUM, velocity=(0.16, -0.06)),)),
    ("Movement unknown", "", (_Example(_MEDIUM, velocity=None, motion_state=MotionState.Unknown),)),
    ("Outline polygon", "", (_Example((0.07, 0.065, 0.11, 0.16), outline=True),)),
    ("Wide", "", (_Example((0.04, 0.09, 0.17, 0.1)),)),
    (
        "Overlapping",
        "",
        (
            _Example((0.05, 0.07, 0.09, 0.13), score=0.91, attributes={"occluded": False}),
            _Example((0.11, 0.095, 0.09, 0.13), score=0.6, attributes={"occluded": True}),
        ),
    ),
    ("At the frame edge", "", (_Example((0.16, 0.12, 0.09, 0.13)),)),
)
"""Sixteen situations in a four by four grid; each box is relative to its cell's top-left corner.

Boxes stay between the caption and the cell's bottom, leaving room for the text a catalog draws above and below them.
The cells are painted over four kinds of ground (see ``grid_background``), so each row and each column is seen over
bright, busy, grey and dark footage. The last one ends at the frame's right and bottom edges, since the last cell is
the frame's bottom-right corner.
"""


def sheets(document: Mapping[str, Any]) -> tuple[Sheet, ...]:
    """Return the Overview, Street, a sheet per object type and relation, and Crowd for a catalog *document*."""
    objects = _recipes(document, OBJECT_GROUPS)
    relations = _recipes(document, ("relations",))
    result = [_overview(document, objects, relations), _street(document, objects, relations)]
    result.extend(_situations(document, recipe) for recipe in objects)
    result.extend(_relation_sheet(document, recipe, objects) for recipe in relations)
    result.append(_crowd(document, objects))
    return tuple(result)


def sheet_frame(sheet: Sheet, catalog: SceneRenderCatalog) -> VideoFrameWithOverlays:
    """Return *sheet* drawn with *catalog* as a frame for the video renderers, with hover details on its objects."""
    frame = VideoFrame(image=sheet.background(), timestamp=0.0, frame_id=0)
    overlays = VideoOverlayData(
        drawing_generator=SheetDrawing(sheet, catalog).prepare,
        timestamp=0.0,
        interaction_provider=CachedSceneOverlay(scene=sheet.scene, catalog=catalog),
    )
    return VideoFrameWithOverlays(frame=frame, overlays=overlays)


def drawing_errors(sheet: Sheet, catalog: SceneRenderCatalog) -> tuple[CatalogDiagnostic, ...]:
    """Draw *sheet* with *catalog* off screen and return the errors drawing reported, such as a failed value check."""
    drawing = SheetDrawing(sheet, catalog)
    context = RenderContext.create(FRAME_WIDTH, FRAME_HEIGHT)
    drawing.prepare(context, DrawingBuffer(DrawingSettings.for_context(context)))
    return drawing.diagnostics


class SheetDrawing:
    """Draw a sheet: its captions as fixed-size text on dark plates, then the catalog's drawing of its Scene.

    Captions are drawn like overlay text rather than into the background image, so they stay sharp at every window
    size and zoom. Their plates keep them readable over bright footage.
    """

    def __init__(self, sheet: Sheet, catalog: SceneRenderCatalog) -> None:
        self._sheet = sheet
        self._catalog = catalog
        self.diagnostics: tuple[CatalogDiagnostic, ...] = ()

    def prepare(self, context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
        """Return the finished drawing for *context*; drawing errors are kept in ``diagnostics``."""
        lines = [self._texts(context, line) for line in _CAPTION_LINES]
        self._plates(context, buffer, lines)
        for line, texts in zip(_CAPTION_LINES, lines):
            self._captions(context, buffer, line, texts)
        diagnostics: list[CatalogDiagnostic] = []
        self._catalog.render_scene(self._sheet.scene, context, buffer, diagnostics=diagnostics)
        self.diagnostics = tuple(diagnostics)
        return buffer.finish()

    def _texts(self, context: RenderContext, line: _CaptionLine) -> list[tuple[str, float]]:
        """Return each caption's text on *line*, shortened to fit, and its width in pixels; empty when it has none."""
        metrics = QFontMetricsF(line.font())
        texts = []
        for caption in self._sheet.captions:
            text = metrics.elidedText(
                line.text(caption), Qt.TextElideMode.ElideRight, caption.width * context.width - 20
            )
            texts.append((text, metrics.horizontalAdvance(text) if text else 0.0))
        return texts

    def _plates(self, context: RenderContext, buffer: DrawingBuffer, lines: list[list[tuple[str, float]]]) -> None:
        shown = [index for index in range(len(self._sheet.captions)) if any(line[index][0] for line in lines)]
        if not shown:
            return
        captions = [self._sheet.captions[index] for index in shown]
        widths = np.array([max(line[index][1] for line in lines) + 12 for index in shown])
        heights = np.array(
            [
                max(line.top + line.size + 6 for line, texts in zip(_CAPTION_LINES, lines) if texts[index][0]) - 2
                for index in shown
            ],
            dtype=np.float64,
        )
        buffer.boxes(
            np.array([caption.x + 4 / context.width for caption in captions]),
            np.array([caption.y + 4 / context.height for caption in captions]),
            widths / context.width,
            heights / context.height,
            Paint(*_PLATE, 0.0, False, *_PLATE, 168, radius=4 / context.scale_factor),
        )

    def _captions(
        self, context: RenderContext, buffer: DrawingBuffer, line: _CaptionLine, texts: list[tuple[str, float]]
    ) -> None:
        shown = [(caption, text) for caption, (text, _width) in zip(self._sheet.captions, texts) if text]
        if not shown:
            return
        buffer.texts(
            np.array([caption.x + 10 / context.width for caption, _text in shown]),
            np.array([caption.y + line.top / context.height for caption, _text in shown]),
            [text for _caption, text in shown],
            ["top-left"] * len(shown),
            Paint(*line.color, font_family=_CAPTION_FONT, font_size=line.size / context.scale_factor),
        )


@dataclass(frozen=True, slots=True)
class _CaptionLine:
    """One line of every caption: which text it shows, and its size, color and distance from the caption's top."""

    detail: bool
    size: int
    top: int
    color: tuple[int, int, int]

    def text(self, caption: Caption) -> str:
        return caption.detail if self.detail else caption.text

    def font(self) -> QFont:
        font = QFont(_CAPTION_FONT)
        font.setPixelSize(self.size)
        return font


_CAPTION_FONT = "Arial"
_CAPTION_LINES = (_CaptionLine(False, 14, 8, (226, 230, 236)), _CaptionLine(True, 11, 27, (160, 167, 178)))
_PLATE = (16, 18, 22)


# Sheets ---------------------------------------------------------------------------------------------------------


def _overview(document: Mapping[str, Any], objects: list[_Recipe], relations: list[_Recipe]) -> Sheet:
    builder = _Builder(document, objects, "overview")
    targets = [*objects, *relations]
    # At most four across, and balanced: six types are two rows of three, not four and two.
    rows = max(1, -(-len(targets) // 4))
    columns = max(1, -(-len(targets) // rows))
    cell_w, cell_h = 1 / columns, 1 / rows
    captions = []
    for index, target in enumerate(targets):
        x, y = (index % columns) * cell_w, (index // columns) * cell_h
        captions.append(Caption(x, y, target.title, target.subtitle, cell_w))
        box = (x + cell_w * 0.3, y + cell_h * 0.25, cell_w * 0.4, cell_h * 0.58)
        if target.group == "relations":
            builder.pair(target, box)
        else:
            builder.add(target, _Example(box, score=0.5 + 0.45 * ((index * 37) % 10) / 10))
    return Sheet(
        "overview",
        "Overview",
        "One of every object type and relation, on an empty street",
        builder.scene,
        tuple(captions),
        empty_street_background,
    )


def _situations(document: Mapping[str, Any], recipe: _Recipe) -> Sheet:
    builder = _Builder(document, [recipe], recipe.key)
    captions = []
    for index, (text, detail, examples) in enumerate(_SITUATIONS):
        x, y = (index % 4) * _CELL_W, (index // 4) * _CELL_H
        captions.append(Caption(x, y, text, detail))
        for example in examples:
            left, top, width, height = example.box
            builder.add(recipe, replace(example, box=(x + left, y + top, width, height)))
    return Sheet(
        recipe.key,
        recipe.title,
        f"{recipe.title} ({recipe.subtitle}) at different sizes, confidence levels and movement, with a long id and "
        "in hard cases, over bright, busy, grey and dark ground",
        builder.scene,
        tuple(captions),
        grid_background,
    )


def _relation_sheet(document: Mapping[str, Any], recipe: _Recipe, objects: list[_Recipe]) -> Sheet:
    builder = _Builder(document, objects, recipe.key)
    boxes = (
        ("Large", (0.12, 0.18, 0.16, 0.7)),
        ("Medium", (0.47, 0.3, 0.1, 0.46)),
        ("Small", (0.76, 0.5, 0.06, 0.26)),
    )
    captions = []
    for text, box in boxes:
        captions.append(Caption(box[0] - 0.01, box[1] - 0.08, text))
        builder.pair(recipe, box)
    return Sheet(
        recipe.key,
        recipe.title,
        f"{recipe.title} ({recipe.subtitle}) between a whole and its part, at three sizes",
        builder.scene,
        tuple(captions),
        empty_street_background,
    )


def _street(document: Mapping[str, Any], objects: list[_Recipe], relations: list[_Recipe]) -> Sheet:
    builder = _Builder(document, objects, "street")
    part_of = any(_PART_OF in relation.types for relation in relations)
    for actor in street_actors():
        whole = builder.add_class(actor.name, _example(actor))
        for part in actor.parts:
            entity = builder.add_class(part.name, _example(part))
            if part_of:
                builder.scene.add_relation(EntityRelation(_PART_OF, whole.id, entity.id))
    return Sheet(
        "street",
        "Street",
        "A camera's view of a sunny street: people near and far with their heads, traffic, overlaps, occlusion, "
        "shade and the frame edge",
        builder.scene,
        (),
        day_street_background,
    )


def _crowd(document: Mapping[str, Any], objects: list[_Recipe]) -> Sheet:
    builder = _Builder(document, objects, "crowd")
    rng = random.Random(7)
    for index in range(48 if objects else 0):
        # Things further up the frame are further away, so smaller, as on a camera looking down a street.
        ground = rng.uniform(0.42, 0.95)
        scale = metre(ground)
        w, h = scale * rng.uniform(0.5, 2.2) * FRAME_HEIGHT / FRAME_WIDTH, scale * rng.uniform(1.0, 1.9)
        box = clipped((rng.uniform(0.0, 1.0) - w / 2, ground - h, w, h))
        moving = rng.random() < 0.65
        velocity = (rng.uniform(-1.5, 1.5) * scale, rng.uniform(-0.5, 0.5) * scale) if moving else None
        motion_state = MotionState.Moving if moving else MotionState.Stationary
        builder.add(objects[index % len(objects)], _Example(box, rng.uniform(0.3, 1.0), velocity, motion_state))
    return Sheet(
        "crowd",
        "Crowd",
        "48 objects of every type on a street at night, to judge clutter",
        builder.scene,
        (),
        night_street_background,
    )


# Building scenes ------------------------------------------------------------------------------------------------


def _recipes(document: Mapping[str, Any], groups: tuple[str, ...]) -> list[_Recipe]:
    recipes = []
    for group in groups:
        for recipe in document.get("recipes", {}).get(group, []):
            selector = recipe.get("selector", {})
            types = ", ".join(str(value) for value in selector.get("types", []))
            subtitle = types or {
                "unclassified": "objects without a class",
                "classified": "classes without their own type",
            }.get(str(selector.get("kind")), str(selector.get("kind", "")))
            title = str(recipe.get("label") or str(recipe.get("id", "")).replace("_", " ").capitalize())
            recipes.append(_Recipe(group, title, subtitle, recipe))
    return recipes


class _Builder:
    """Add example objects to a Scene, classified so the catalog routes each to the intended recipe.

    Ids and attribute values come from generators seeded by the sheet, so a sheet looks the same every time, and
    adding a binding to a catalog does not change the ids.
    """

    def __init__(self, document: Mapping[str, Any], objects: list[_Recipe], seed: str) -> None:
        self._document = document
        self._objects = objects
        self._ids = random.Random(f"{seed} ids")
        self._values = random.Random(f"{seed} values")
        self.scene = Scene(time_slice=TimeSlice(start=0, end=1))

    def add(self, recipe: _Recipe | None, example: _Example) -> Entity:
        """Add *example* classified so that *recipe* draws it."""
        return self._add(self._class_name(recipe), recipe, example)

    def add_class(self, name: str | None, example: _Example) -> Entity:
        """Add *example* as a detection of class *name*, or without a class; the catalog decides how it is drawn."""
        return self._add(name, self._route(name), example)

    def pair(self, relation: _Recipe, box: tuple[float, float, float, float]) -> None:
        """Add a person standing in *box* and their head, related by *relation*, the way decoders pair them."""
        x, y, w, h = box
        width = min(w, h * 0.4 * FRAME_HEIGHT / FRAME_WIDTH)
        left = x + (w - width) / 2
        source = self.add_class("human", _Example((left, y, width, h), 0.9, None, None))
        head = (left + width * 0.3, y + h * 0.01, width * 0.4, h * 0.13)
        target = self.add_class("head", _Example(head, 0.8, None, None))
        for kind in relation.types[:1]:
            self.scene.add_relation(EntityRelation(kind, source.id, target.id))

    def _add(self, name: str | None, recipe: _Recipe | None, example: _Example) -> Entity:
        entity = Entity(id=self._new_id(example.id), motion_state=example.motion_state)
        velocity = ImageVelocity(*example.velocity) if example.velocity is not None else None
        classification = (
            [Classification(type=name, score=Score(example.score), attributes=self._attributes(recipe, example))]
            if name is not None
            else []
        )
        entity.add_observation(
            Observation(
                geometry=_geometry(example.box, example.outline),
                classification=classification,
                frame_number=0,
                velocity_in_image_space=velocity,
            )
        )
        self.scene.add_entity(entity)
        return entity

    def _new_id(self, wanted: str) -> EntityId:
        """Return *wanted*, or a new id in one of the usual formats; never one the scene already has."""
        candidate = wanted
        while not candidate or candidate in self.scene.entities:
            candidate = self._ids.choices(_ID_FORMATS, _ID_WEIGHTS)[0](self._ids)
        return EntityId(candidate)

    def _route(self, name: str | None) -> _Recipe | None:
        """Return the recipe the catalog draws class *name* with, or objects without a class when it is None."""
        kind = "unclassified" if name is None else "classified"
        own = (recipe for recipe in self._objects if name in recipe.types)
        fallback = (recipe for recipe in self._objects if recipe.selector.get("kind") == kind)
        return next(chain(own, fallback), None)

    def _class_name(self, recipe: _Recipe | None) -> str | None:
        kind = recipe.selector.get("kind") if recipe is not None else None
        if recipe is None or kind == "unclassified":
            return None
        if kind == "classified":
            claimed = {
                str(value)
                for candidate in self._document.get("recipes", {}).get("classifications", [])
                for value in candidate.get("selector", {}).get("types", [])
            }
            candidates = chain(_FALLBACK_NAMES, (f"example_{number}" for number in count(2)))
            return next(name for name in candidates if name not in claimed)
        # A type covers several classes, such as cars, buses and bikes; examples show them all.
        return self._values.choice(recipe.types) if recipe.types else "example"

    def _attributes(self, recipe: _Recipe | None, example: _Example) -> list[Attribute]:
        """Return a value for every attribute *recipe* reads, then the attributes *example* sets itself."""
        values: dict[str, Any] = {}
        for binding in (recipe.data.get("bindings", {}) if recipe is not None else {}).values():
            name = str(binding.get("attribute", ""))
            values[name] = _VALUES[str(binding.get("value_shape"))](name, self._values)
        values.update(example.attributes)
        return [Attribute(name, value) for name, value in values.items()]


def _example(actor: Actor) -> _Example:
    """Return *actor* as a detection reports it: cut to the frame, still unless it moves."""
    state = MotionState.Moving if actor.velocity is not None else MotionState.Stationary
    return _Example(clipped(actor.box), actor.score, actor.velocity, state, attributes=actor.attributes)


def _geometry(box: tuple[float, float, float, float], outline: bool) -> Geometry:
    x, y, w, h = box
    if not outline:
        return BoundingBox.from_xywh(x, y, w, h)
    points = ((0.2, 0.0), (0.85, 0.08), (1.0, 0.7), (0.55, 1.0), (0.0, 0.8))
    return Polygon(points=[NormalizedPoint(x + w * px, y + h * py) for px, py in points])


# Realistic detection data -----------------------------------------------------------------------------------------


_ID_FORMATS: tuple[Callable[[random.Random], str], ...] = (
    lambda rng: str(uuid.UUID(int=rng.getrandbits(128), version=4)),
    lambda rng: str(rng.randint(1, 99)),
    lambda rng: str(rng.randint(100, 99999)),
    lambda rng: f"track-{rng.randint(0, 999999):06d}",
    lambda rng: f"obj_{rng.randint(1, 400)}_cam{rng.randint(1, 4)}",
    lambda rng: f"{rng.getrandbits(16 * rng.choice((1, 2))):x}",
)
"""Track id formats seen from decoders: UUIDs, small and large counters, prefixed track names and short hex."""
_ID_WEIGHTS = (5, 2, 2, 1, 1, 1)

_PALETTES: Mapping[str, tuple[tuple[str, int], ...]] = {
    "upper_clothing_colors": (
        ("black", 26),
        ("white", 14),
        ("gray", 14),
        ("blue", 14),
        ("red", 8),
        ("green", 6),
        ("beige", 5),
        ("brown", 4),
        ("yellow", 3),
        ("pink", 3),
        ("orange", 2),
        ("purple", 1),
    ),
    "lower_clothing_colors": (
        ("black", 30),
        ("blue", 28),
        ("gray", 16),
        ("beige", 9),
        ("brown", 7),
        ("white", 5),
        ("green", 3),
        ("red", 2),
    ),
    "vehicle_colors": (
        ("white", 25),
        ("black", 18),
        ("gray", 17),
        ("silver", 15),
        ("blue", 9),
        ("red", 8),
        ("brown", 3),
        ("green", 2),
        ("yellow", 2),
        ("orange", 1),
    ),
}
"""How often each color is reported for clothing and vehicles; other color attributes use any color equally."""
_ANY_COLOR = tuple((name, 1) for name in ("black", "white", "gray", "blue", "red", "green", "yellow", "brown"))
_TRUE_SHARE: Mapping[str, float] = {"carries_bag": 0.3, "occluded": 0.15, "keyframe": 0.1}
"""How often a flag is set; other flags are set three times in ten."""
_NUMBERS: Mapping[str, Callable[[random.Random], float]] = {
    # Faces are mostly either clearly visible or turned away.
    "face_visible": lambda rng: round(rng.betavariate(0.7, 0.7), 2),
    "z_order": lambda rng: float(rng.randint(0, 4)),
}
"""Plausible ranges of number attributes decoders report; others are a share between 0.05 and 0.95."""


def _share(rng: random.Random) -> float:
    return round(rng.uniform(0.05, 0.95), 2)


def _color_value(name: str, rng: random.Random) -> list[ColorClassification]:
    palette = _PALETTES.get(name, _ANY_COLOR)
    wanted = rng.choices((1, 2, 3), (55, 35, 10))[0]
    names: list[str] = []
    while len(names) < wanted:
        color = rng.choices([color for color, _weight in palette], [weight for _color, weight in palette])[0]
        if color not in names:
            names.append(color)
    score = rng.uniform(0.45, 0.95)
    colors = []
    for color in names:
        colors.append(ColorClassification(color, rgb_from_map(color), Score(round(score, 2))))
        score *= rng.uniform(0.3, 0.7)
    return colors


_VALUES: Mapping[str, Callable[[str, random.Random], Any]] = {
    "bool": lambda name, rng: rng.random() < _TRUE_SHARE.get(name, 0.3),
    "number": lambda name, rng: _NUMBERS.get(name, _share)(rng),
    "color_classification_list": _color_value,
}
"""A plausible value for an attribute, by the shape a binding reads it as."""
