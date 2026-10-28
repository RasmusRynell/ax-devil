"""Authoritative value, operation and primitive definitions for compiler and editor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from .values import TemplateRuntimeError

MAX_DEPTH = 64
MAX_TEMPLATE_DEPTH = 32
MAX_EXPANDED_STEPS = 10000
MAX_COLLECTION = 4096
MAX_DOCUMENT_BYTES = 2_000_000
COMPILER_REVISION = 10


@dataclass(frozen=True, slots=True)
class ValueType:
    """A closed structural type with explicit nullability, collection contents and allowed values.

    Bounds use JSON Schema's names. Numbers use ``minimum``, ``exclusive_minimum`` and ``maximum``; text uses
    ``max_length``; lists use ``min_items`` and ``max_items``. A record's bounds live on its field types.
    """

    name: str
    fields: tuple[tuple[str, ValueType], ...] = ()
    item: ValueType | None = None
    nullable: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    exclusive_minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None

    @property
    def bounds(self) -> dict[str, float]:
        """Return the bounds this type declares, keyed by their JSON Schema name."""
        return {
            name: value
            for name, value in (
                ("minimum", self.minimum),
                ("exclusiveMinimum", self.exclusive_minimum),
                ("maximum", self.maximum),
                ("maxLength", self.max_length),
                ("minItems", self.min_items),
                ("maxItems", self.max_items),
            )
            if value is not None
        }

    @property
    def is_scalar(self) -> bool:
        """Return whether values of this type are single numbers, booleans or text."""
        return not self.fields and self.item is None

    def optional(self) -> ValueType:
        """Return the nullable form of this type."""
        return replace(self, nullable=True)

    def present(self) -> ValueType:
        """Return the non-null form of this type."""
        return replace(self, nullable=False)

    def field(self, name: str) -> ValueType:
        """Resolve a declared field, rejecting arbitrary traversal."""
        for key, value_type in self.fields:
            if key == name:
                return value_type
        raise TemplateRuntimeError(f"Unknown field '{name}' on {self.name}.", code="unknown_reference")


NUMBER = ValueType("number")
INTEGER = ValueType("integer")
BOOL = ValueType("bool")
STRING = ValueType("string")
NULL = ValueType("null", nullable=True)
ANY = ValueType("any", nullable=True)
NONNEGATIVE = replace(NUMBER, minimum=0)
CHANNEL = replace(INTEGER, minimum=0, maximum=255)
RGB = ValueType("rgb", item=CHANNEL, min_items=3, max_items=3)
POINT = ValueType("image_point", (("x", NUMBER), ("y", NUMBER)))
BOX = ValueType("image_box", (("x", NUMBER), ("y", NUMBER), ("w", NONNEGATIVE), ("h", NONNEGATIVE)))
VECTOR = ValueType("image_vector", (("vx", NUMBER), ("vy", NUMBER)))
POINTS = ValueType("image_point_list", item=POINT)
LENGTH = ValueType(
    "length",
    (("value", NUMBER), ("unit", ValueType("unit", choices=("px", "image_width", "image_height", "image_min")))),
)
NONNEGATIVE_LENGTH = replace(LENGTH, fields=(("value", NONNEGATIVE), LENGTH.fields[1]))
POSITIVE_LENGTH = replace(LENGTH, fields=(("value", replace(NUMBER, exclusive_minimum=0)), LENGTH.fields[1]))
ARROW = ValueType("image_arrow", (("start", POINT), ("end", POINT), ("head_points", POINTS)))
PEN_STYLE = ValueType("pen_style", choices=("solid", "dash"))
ELISION = ValueType("elision", choices=("end", "middle"))
ANCHOR = ValueType(
    "anchor",
    choices=(
        "top-left",
        "top-center",
        "top-right",
        "center-left",
        "center",
        "center-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
        "top",
        "bottom",
        "left",
        "right",
    ),
)
TEXT_ANCHOR = replace(ANCHOR, name="text_anchor", choices=(*ANCHOR.choices, "baseline"))
CORNER = ValueType("corner_position", choices=("top-left", "top-right", "bottom-left", "bottom-right"))
STROKE = ValueType("stroke", (("color", RGB), ("width", NONNEGATIVE_LENGTH), ("pattern", PEN_STYLE)))
FILL = ValueType("fill", (("color", RGB), ("alpha", CHANNEL)))
STYLE = ValueType(
    "shape_style", (("stroke", STROKE.optional()), ("fill", FILL.optional()), ("radius", NONNEGATIVE_LENGTH.optional()))
)
TEXT_STYLE = ValueType("text_style", (("color", RGB), ("size", POSITIVE_LENGTH), ("family", STRING.optional())))
TEXT_STYLE_WRAPPER = ValueType("text_style_wrapper", (("text", TEXT_STYLE),))
LABEL_WEIGHT = ValueType("label_weight", choices=("regular", "medium", "semibold", "bold"))
LABEL_RUN = ValueType(
    "label_run",
    (("text", STRING.optional()), ("bar", NUMBER.optional()), ("color", RGB), ("weight", LABEL_WEIGHT)),
)
LABEL_RUNS = ValueType("label_runs", item=LABEL_RUN, min_items=1, max_items=8)
LABEL_STYLE = ValueType(
    "label_style",
    (
        ("size", POSITIVE_LENGTH),
        ("family", STRING.optional()),
        ("background", FILL.optional()),
        ("padding_x", NONNEGATIVE_LENGTH),
        ("padding_y", NONNEGATIVE_LENGTH),
        ("radius", NONNEGATIVE_LENGTH),
        ("gap", NONNEGATIVE_LENGTH),
    ),
)
LABEL_STYLE_WRAPPER = ValueType("label_style_wrapper", (("label", LABEL_STYLE),))
COLOR_STOP = ValueType("color_stop", (("at", NUMBER), ("color", RGB)))
COLOR_STOPS = ValueType("color_stops", item=COLOR_STOP, min_items=1)
PALETTE = ValueType("palette", item=RGB, min_items=1)
TEXT_ENTRY = ValueType("text_entry", (("key", STRING), ("text", STRING)))
TEXT_MAPPING = ValueType("text_mapping", item=TEXT_ENTRY)
CONTEXT = ValueType(
    "context",
    tuple((name, NUMBER) for name in ("width", "height", "aspect_ratio", "scale_factor", "px_h", "px_v", "px_min")),
)
ENTITY = ValueType("entity", (("id", STRING), ("motion_state", STRING.optional())))
OBSERVATION = ValueType(
    "observation", (("geometry", BOX), ("polygon_points", POINTS.optional()), ("velocity", VECTOR.optional()))
)
CLASSIFICATION = ValueType("classification", (("type", STRING), ("score", NUMBER.optional())))
SCENE = ValueType(
    "scene", (("entity", ENTITY), ("observation", OBSERVATION), ("classification", CLASSIFICATION.optional()))
)
ENDPOINT = ValueType(
    "endpoint",
    (
        ("entity", ValueType("endpoint_entity", (("id", STRING),))),
        ("observation", ValueType("endpoint_observation", (("geometry", BOX),))),
    ),
)
RELATION_SCENE = ValueType(
    "relation_scene",
    (("relation", ValueType("relation", (("type", STRING),))), ("source", ENDPOINT), ("target", ENDPOINT)),
)
COLOR_BINDING = ValueType("color_binding", (("color", RGB), ("score", NUMBER)))
TYPES = MappingProxyType(
    {
        item.name: item
        for item in (
            NUMBER,
            INTEGER,
            BOOL,
            STRING,
            RGB,
            POINT,
            BOX,
            VECTOR,
            POINTS,
            LENGTH,
            ARROW,
            PEN_STYLE,
            ELISION,
            ANCHOR,
            TEXT_ANCHOR,
            CORNER,
            STYLE,
            COLOR_STOPS,
            PALETTE,
            LABEL_WEIGHT,
            LABEL_RUNS,
        )
    }
)


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Named inputs and return contract; polymorphic results are resolved by the compiler."""

    name: str
    arguments: tuple[tuple[str, ValueType], ...]
    result: ValueType
    defaults: tuple[tuple[str, object], ...] = ()


def _operation(
    name: str, result: ValueType, *, omitted: tuple[str, ...] = (), **arguments: ValueType
) -> OperationDefinition:
    return OperationDefinition(name, tuple(arguments.items()), result, tuple((key, None) for key in omitted))


OPERATIONS = MappingProxyType(
    {
        definition.name: definition
        for definition in (
            OperationDefinition("if", (("condition", BOOL), ("then", ANY), ("else", ANY)), ANY),
            _operation("coalesce", ANY, values=ValueType("list", item=ANY)),
            *(_operation(name, ANY, values=ValueType("list", item=ANY)) for name in ("add", "mul", "min", "max")),
            _operation("sub", ANY, left=ANY, right=ANY),
            _operation("div", ANY, numerator=ANY, denominator=ANY),
            _operation("clamp", ANY, value=ANY, minimum=ANY, maximum=ANY),
            _operation("gt", BOOL, left=NUMBER, right=NUMBER),
            _operation("is_present", BOOL, value=ANY),
            _operation("is_not_empty", BOOL, value=ANY),
            _operation("has_area", BOOL, geometry=BOX),
            _operation("box_anchor", POINT, geometry=BOX, anchor=ANCHOR),
            _operation("box_min_size", LENGTH, geometry=BOX),
            _operation("offset_point", POINT, point=POINT, dx=LENGTH, dy=LENGTH),
            _operation(
                "inset_box",
                BOX,
                omitted=("margin", "fraction"),
                geometry=BOX,
                margin=LENGTH.optional(),
                fraction=NUMBER.optional(),
            ),
            _operation(
                "resize_box",
                BOX,
                omitted=("width", "height"),
                geometry=BOX,
                width=LENGTH.optional(),
                height=LENGTH.optional(),
                anchor=ANCHOR,
            ),
            _operation("scale_box", BOX, geometry=BOX, x_factor=NUMBER, y_factor=NUMBER, anchor=ANCHOR),
            _operation("place_box", BOX, container=BOX, width=LENGTH, height=LENGTH, anchor=ANCHOR, margin=LENGTH),
            _operation(
                "vector_arrow",
                ARROW.optional(),
                origin=POINT,
                vector=VECTOR.optional(),
                gain=NUMBER,
                max_length=LENGTH,
                head_length=LENGTH,
                head_half_width=LENGTH,
                min_magnitude=NUMBER,
            ),
            _operation(
                "trim_text",
                STRING.optional(),
                omitted=("elide",),
                text=STRING.optional(),
                max_length=INTEGER,
                delimiter=STRING.optional(),
                suffix=STRING,
                elide=ELISION.optional(),
            ),
            _operation("number_text", STRING.optional(), value=NUMBER.optional(), precision=INTEGER, hide_zero=BOOL),
            _operation("lookup_text", STRING.optional(), text=STRING.optional(), mapping=TEXT_MAPPING, default=STRING),
            _operation("color_ramp", RGB, value=NUMBER, stops=COLOR_STOPS),
            _operation("pick_color", RGB, text=STRING, colors=PALETTE),
        )
    }
)
PRIMITIVES = MappingProxyType(
    {
        "box": ValueType("box_fields", (("geometry", BOX),)),
        "line": ValueType("line_fields", (("start", POINT), ("end", POINT))),
        "polygon": ValueType("polygon_fields", (("points", POINTS),)),
        "polyline": ValueType("polyline_fields", (("points", POINTS),)),
        "point": ValueType("point_fields", (("position", POINT),)),
        "circle": ValueType("circle_fields", (("center", POINT), ("radius", NONNEGATIVE_LENGTH))),
        "text": ValueType("text_fields", (("position", POINT), ("text", STRING), ("anchor", TEXT_ANCHOR))),
        "label": ValueType("label_fields", (("position", POINT), ("anchor", ANCHOR), ("runs", LABEL_RUNS))),
    }
)
STYLES: Mapping[str, ValueType] = MappingProxyType({"text": TEXT_STYLE_WRAPPER, "label": LABEL_STYLE_WRAPPER})
"""Style types of the primitives that are not shapes; every other primitive takes a shape style."""


PARAMETER_BOUNDS = ("minimum", "exclusive_minimum", "maximum", "max_length")
"""Bounds a template parameter declaration may set on its type."""

QUANTITIES: Mapping[str, str] = MappingProxyType({"length": "value"})
"""Record types whose bounds apply to one numeric field, keyed by type name."""


def authoring_definitions() -> dict[str, object]:
    """Describe backend types, arguments and limits as JSON-serializable editor data."""

    def describe(kind: ValueType) -> dict[str, object]:
        result: dict[str, object] = {"type": kind.name, "nullable": kind.nullable}
        if kind.fields:
            result["fields"] = {name: describe(value_type) for name, value_type in kind.fields}
        if kind.item is not None:
            result["items"] = describe(kind.item)
        if kind.choices:
            result["choices"] = list(kind.choices)
        result.update(kind.bounds)
        return result

    return {
        "schema_version": 3,
        "compiler_revision": COMPILER_REVISION,
        "types": {name: describe(kind) for name, kind in TYPES.items()},
        "operations": {
            name: {
                "arguments": {
                    key: {
                        **describe(kind),
                        "required": key not in dict(operation.defaults),
                        **({"default": dict(operation.defaults)[key]} if key in dict(operation.defaults) else {}),
                    }
                    for key, kind in operation.arguments
                },
                "result": describe(operation.result),
            }
            for name, operation in OPERATIONS.items()
        },
        "primitives": {
            name: {"fields": describe(kind), "style": describe(STYLES.get(name, STYLE))}
            for name, kind in PRIMITIVES.items()
        },
        "entity_scene": describe(SCENE),
        "relation_scene": describe(RELATION_SCENE),
        "context": describe(CONTEXT),
        "limits": {
            "document_bytes": MAX_DOCUMENT_BYTES,
            "expression_depth": MAX_DEPTH,
            "template_depth": MAX_TEMPLATE_DEPTH,
            "expanded_steps": MAX_EXPANDED_STEPS,
            "collection_items": MAX_COLLECTION,
        },
    }
