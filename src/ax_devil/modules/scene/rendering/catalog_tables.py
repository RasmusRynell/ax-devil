"""Row-column inputs of a recipe: Scene entities and relations read once per column, with typed bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from ax_devil.modules.scene.model import Classification, ColorClassification, Entity, EntityRelation, Observation
from ax_devil.modules.scene.rendering.template_runtime.definitions import (
    ValueType,
)
from ax_devil.modules.scene.rendering.template_runtime.kernels import objects_from


@dataclass(frozen=True, slots=True)
class RecipeBinding:
    """A typed, explicitly declared classification attribute projection."""

    attribute: str
    kind: str
    value_type: ValueType

    def value(self, classification: Classification | None) -> object:
        """Read only the requested attribute; tables validate whole columns at once."""
        if classification is None:
            return None
        if self.kind == "bool":
            return classification.optional_bool_attribute(self.attribute)
        if self.kind == "number":
            return classification.optional_float_attribute(self.attribute)
        color = max(
            classification.attribute_items(self.attribute, ColorClassification),
            key=lambda item: item.score.value,
            default=None,
        )
        return None if color is None else ((color.rgb.r, color.rgb.g, color.rgb.b), color.score.value)


Column = tuple[object, object]
Failures = tuple[tuple[object, tuple[str, str, str]], ...]
Rows = NDArray[np.bool_]


def _present(values: Sequence[object]) -> Rows:
    return np.fromiter((value is not None for value in values), dtype=np.bool_, count=len(values))


def _finite_rows(message: str, *columns: NDArray[np.float64]) -> Failures:
    ok = np.all([np.isfinite(column) for column in columns], axis=0)
    return () if ok.all() else ((~ok, ("invalid_value", "$", message)),)


def _geometry(observations: Sequence[Observation]) -> tuple[Column, Failures]:
    boxes = np.array([observation.geometry.as_xywh() for observation in observations], dtype=np.float64).reshape(-1, 4)
    x, y, w, h = boxes.T
    failures = _finite_rows("number must be finite.", x, y, w, h)
    negative = (w < 0) | (h < 0)
    if negative.any():
        failures += ((negative, ("invalid_value", "$", "Box dimensions must be nonnegative.")),)
    return (True, {"x": (True, x), "y": (True, y), "w": (True, w), "h": (True, h)}), failures


def _ids(entities: Sequence[Entity]) -> tuple[Column, Failures]:
    return (True, objects_from([str(entity.id) for entity in entities])), ()


def _record(*parts: tuple[str, tuple[Column, Failures]]) -> tuple[Column, Failures]:
    return (True, {name: column for name, (column, _) in parts}), tuple(
        failure for _, (_, failures) in parts for failure in failures
    )


def _motion_state(entity: Entity) -> str | None:
    if entity.motion_state is None:
        return None
    return entity.motion_state.value


def _polygon(observation: Observation) -> object:
    points = observation.geometry.polygon_points()
    return None if points is None else tuple({"x": point.x, "y": point.y} for point in points)


class Table:
    """Lazily extracted, validated columns for the rows routed to one recipe.

    A column is ``(present, data)``: data is an array, a tuple of RGB channel arrays, or a mapping of
    field columns whose presence includes their parent's. Failures are row masks with checks.
    """

    def __init__(self, count: int) -> None:
        self.count = count
        self._columns: dict[tuple[str, ...], tuple[Column, Failures]] = {}

    def column(self, path: tuple[str, ...]) -> tuple[Column, Failures]:
        """Return the column for a reference path, extracting it on first demand."""
        cached = self._columns.get(path)
        if cached is None:
            extracted = self._extract(path)
            if extracted is None:
                parent, failures = self.column(path[:-1])
                extracted = cast(Mapping[str, Column], parent[1])[path[-1]], failures
            cached = self._columns[path] = extracted
        return cached

    def _extract(self, path: tuple[str, ...]) -> tuple[Column, Failures] | None:
        raise NotImplementedError


class EntityTable(Table):
    def __init__(
        self,
        rows: Sequence[tuple[Entity, Observation, Classification | None]],
        bindings: Mapping[str, RecipeBinding],
    ) -> None:
        super().__init__(len(rows))
        self._entities = [row[0] for row in rows]
        self._observations = [row[1] for row in rows]
        self._classifications = [row[2] for row in rows]
        self._bindings = bindings

    def _extract(self, path: tuple[str, ...]) -> tuple[Column, Failures] | None:
        if path[0] == "bindings":
            return self._binding(self._bindings[path[1]]) if len(path) == 2 else None
        match path[1:]:
            case ("entity", "id"):
                return _ids(self._entities)
            case ("entity", "motion_state"):
                texts = [_motion_state(entity) for entity in self._entities]
                present = _present(texts)
                return (present, objects_from([text or "" for text in texts])), ()
            case ("entity",):
                return _record(
                    ("id", self.column((*path, "id"))),
                    ("motion_state", self.column((*path, "motion_state"))),
                )
            case ("observation", "geometry"):
                return _geometry(self._observations)
            case ("observation", "polygon_points"):
                polygons = [_polygon(observation) for observation in self._observations]
                return (_present(polygons), objects_from([polygon or () for polygon in polygons])), ()
            case ("observation", "velocity"):
                velocities = [observation.velocity_in_image_space for observation in self._observations]
                present = _present(velocities)
                vx = np.array([0.0 if v is None else v.vx for v in velocities], dtype=np.float64)
                vy = np.array([0.0 if v is None else v.vy for v in velocities], dtype=np.float64)
                return (present, {"vx": (present, vx), "vy": (present, vy)}), _finite_rows(
                    "number must be finite.", vx, vy
                )
            case ("observation",):
                return _record(
                    *((name, self.column((*path, name))) for name in ("geometry", "polygon_points", "velocity"))
                )
            case ("classification", "type"):
                present = _present(self._classifications)
                types = objects_from(["" if item is None else item.type for item in self._classifications])
                return (present, types), ()
            case ("classification", "score" | "confidence" as name):
                values = [None if item is None else getattr(item, name) for item in self._classifications]
                present = _present(values)
                numbers = np.array([0.0 if value is None else value.value for value in values], dtype=np.float64)
                return (present, numbers), _finite_rows("number must be finite.", numbers)
            case ("classification",):
                column, failures = _record(
                    *((name, self.column((*path, name))) for name in ("type", "score", "confidence"))
                )
                return (_present(self._classifications), column[1]), failures
            case ():
                return _record(
                    *((name, self.column((*path, name))) for name in ("entity", "observation", "classification"))
                )
        return None

    def _binding(self, binding: RecipeBinding) -> tuple[Column, Failures]:
        values = [binding.value(classification) for classification in self._classifications]
        present = _present(values)
        if binding.kind == "bool":
            return (present, np.array([value is True for value in values], dtype=np.bool_)), ()
        if binding.kind == "number":
            numbers = np.array([0.0 if value is None else value for value in values], dtype=np.float64)
            return (present, numbers), _finite_rows("number must be finite.", numbers)
        pairs = [cast(tuple[tuple[int, int, int], float], value) for value in values]
        channels = np.array([(0, 0, 0) if pair is None else pair[0] for pair in pairs], dtype=np.float64).reshape(-1, 3)
        scores = np.array([0.0 if pair is None else pair[1] for pair in pairs], dtype=np.float64)
        failures = _finite_rows("number must be finite.", scores)
        invalid = present & ~np.all((channels >= 0) & (channels <= 255) & (channels == np.floor(channels)), axis=1)
        if invalid.any():
            failures += (
                (
                    invalid,
                    ("invalid_value", "$", "RGB requires three integer channels in [0,255]."),
                ),
            )
        column = (
            present,
            {"color": (present, (channels[:, 0], channels[:, 1], channels[:, 2])), "score": (present, scores)},
        )
        return column, failures


class RelationTable(Table):
    def __init__(self, rows: Sequence[tuple[EntityRelation, Entity, Observation, Entity, Observation]]) -> None:
        super().__init__(len(rows))
        self._relations = [row[0] for row in rows]
        self._sides = {
            "source": ([row[1] for row in rows], [row[2] for row in rows]),
            "target": ([row[3] for row in rows], [row[4] for row in rows]),
        }

    def _extract(self, path: tuple[str, ...]) -> tuple[Column, Failures] | None:
        match path[1:]:
            case ("relation", "type"):
                return (True, objects_from([relation.type for relation in self._relations])), ()
            case ("relation",):
                return _record(("type", self.column((*path, "type"))))
            case (side, "entity", "id"):
                return _ids(self._sides[side][0])
            case (side, "entity"):
                return _record(("id", self.column((*path, "id"))))
            case (side, "observation", "geometry"):
                return _geometry(self._sides[side][1])
            case (side, "observation"):
                return _record(("geometry", self.column((*path, "geometry"))))
            case (side,) if side in self._sides:
                return _record(
                    ("entity", self.column((*path, "entity"))), ("observation", self.column((*path, "observation")))
                )
            case ():
                return _record(*((name, self.column((*path, name))) for name in ("relation", "source", "target")))
        return None
