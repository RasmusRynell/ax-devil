"""Catalog-owned scene routing, validated loading and per-recipe row-column execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field, replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from numpy.typing import NDArray

from ax_devil.modules.scene.model import Classification, ColorClassification, Entity, EntityRelation, Observation, Scene
from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler, _keys, _list, _object
from ax_devil.modules.scene.rendering.template_runtime.definitions import (
    BOOL,
    COLOR_BINDING,
    COMPILER_REVISION,
    CONTEXT,
    MAX_COLLECTION,
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    NUMBER,
    RELATION_SCENE,
    SCENE,
    ValueType,
)
from ax_devil.modules.scene.rendering.template_runtime.kernels import RowState, emit, objects_from
from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog, RenderProgram
from ax_devil.modules.scene.rendering.template_runtime.schema import catalog_schema
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic, TemplateRuntimeError, freeze
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
from ax_devil.modules.video_player.engine.drawing import DrawingTarget
from ax_devil.modules.video_player.engine.render_context import RenderContext

CatalogJsonDocument = Mapping[str, Any]

BUILT_IN_CATALOG_PATHS = tuple(
    Path(__file__).with_name("catalog_definitions") / f"{name}.json"
    for name in ("standard", "minimal", "chunky", "glass", "tracking", "classic")
)
"""The catalogs that come with the app, in the order they are listed. They are read in place, never copied into the
user catalog store and never written."""
BUILT_IN_CATALOG_PATH = BUILT_IN_CATALOG_PATHS[0]
"""The built-in catalog new views start from until the user chooses another, and the fallback when that one fails."""


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


class _Table:
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


class _EntityTable(_Table):
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
            case ("classification", "score"):
                present = _present(self._classifications)
                scores = np.array(
                    [0.0 if item is None else item.score.value for item in self._classifications], dtype=np.float64
                )
                return (present, scores), _finite_rows("number must be finite.", scores)
            case ("classification",):
                column, failures = _record(*((name, self.column((*path, name))) for name in ("type", "score")))
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


class _RelationTable(_Table):
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


def _run(
    update: Callable[..., object],
    table: _Table,
    context: RenderContext,
    output: DrawingTarget,
    recipe_id: str,
    diagnostics: list[CatalogDiagnostic] | None,
) -> None:
    """Evaluate one recipe for all its rows; failed rows emit nothing and report diagnostics."""
    state = RowState(table.count)
    try:
        with np.errstate(all="ignore"):
            update(table, context, state)
    except (ArithmeticError, TypeError, KeyError, ValueError) as error:
        state.fail[:] = True
        state.hits.append(("evaluation_error", "$", str(error)))
    emit(output, state)
    if diagnostics is None:
        return
    for code, location, message in state.hits:
        location = location if location != "$" else f"$.recipes.{recipe_id}"
        if len(diagnostics) < 256 and not any(item.code == code and item.location == location for item in diagnostics):
            diagnostics.append(CatalogDiagnostic(code, location, message, recipe_id))


@dataclass(frozen=True, slots=True)
class CompiledSceneRenderRecipe:
    """One compiled entity recipe, evaluated for all entities routed to it at once."""

    recipe_id: str
    _program: InitVar[RenderProgram]
    bindings: Mapping[str, RecipeBinding]

    _update: Callable[..., object] = field(init=False, repr=False, compare=False)

    def __post_init__(self, _program: RenderProgram) -> None:
        object.__setattr__(self, "_update", _program.compile_table())

    def render_rows(
        self,
        rows: Sequence[tuple[Entity, Observation, Classification | None]],
        context: RenderContext,
        output: DrawingTarget,
        diagnostics: list[CatalogDiagnostic] | None = None,
    ) -> None:
        """Render observed entities, reading only demanded Scene fields once per column."""
        _run(self._update, _EntityTable(rows, self.bindings), context, output, self.recipe_id, diagnostics)


@dataclass(frozen=True, slots=True)
class CompiledSceneRelationRenderRecipe:
    """One compiled relation recipe over visible endpoints."""

    recipe_id: str
    _program: InitVar[RenderProgram]

    _update: Callable[..., object] = field(init=False, repr=False, compare=False)

    def __post_init__(self, _program: RenderProgram) -> None:
        object.__setattr__(self, "_update", _program.compile_table())

    def render_rows(
        self,
        rows: Sequence[tuple[EntityRelation, Entity, Observation, Entity, Observation]],
        context: RenderContext,
        output: DrawingTarget,
        diagnostics: list[CatalogDiagnostic] | None = None,
    ) -> None:
        """Render relations whose endpoints are both observed."""
        _run(self._update, _RelationTable(rows), context, output, self.recipe_id, diagnostics)


@dataclass(frozen=True, slots=True)
class SceneRenderCatalog:
    """Immutable compiled recipes, independent of consumer state and diagnostics."""

    catalog_id: str
    content_hash: str
    templates: RenderCatalog
    fallback_recipes: Mapping[str, CompiledSceneRenderRecipe]
    classification_recipes: Mapping[str, CompiledSceneRenderRecipe]
    relation_recipes: Mapping[str, CompiledSceneRelationRenderRecipe]
    semantic_hash: str = ""
    supported_features: frozenset[OverlayFeature] = frozenset()

    def __post_init__(self) -> None:
        for name in ("fallback_recipes", "classification_recipes", "relation_recipes"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def rendering_identity(self) -> tuple[str, str]:
        """Return semantic identity, separate from descriptive document changes."""
        return f"compiler:{COMPILER_REVISION}", self.semantic_hash or self.content_hash

    def render_scene(
        self,
        scene: Scene,
        context: RenderContext,
        output: DrawingTarget,
        *,
        diagnostics: list[CatalogDiagnostic] | None = None,
    ) -> None:
        """Evaluate each recipe once for all its entities, then relations; invalid rows emit nothing."""
        entities: dict[
            int, tuple[CompiledSceneRenderRecipe, list[tuple[Entity, Observation, Classification | None]]]
        ] = {}
        for entity in scene.entities.values():
            observation = entity.latest_observation
            if observation is None:
                continue
            classification = observation.primary_classification
            recipe = self._route(classification)
            entities.setdefault(id(recipe), (recipe, []))[1].append((entity, observation, classification))
        for recipe, rows in entities.values():
            recipe.render_rows(rows, context, output, diagnostics)
        relations: dict[
            int,
            tuple[
                CompiledSceneRelationRenderRecipe, list[tuple[EntityRelation, Entity, Observation, Entity, Observation]]
            ],
        ] = {}
        for relation in sorted(
            scene.relations, key=lambda item: (item.type, item.source_entity_id, item.target_entity_id)
        ):
            relation_recipe = self.relation_recipes.get(relation.type)
            source = scene.entities.get(relation.source_entity_id)
            target = scene.entities.get(relation.target_entity_id)
            if relation_recipe is None or source is None or target is None:
                continue
            source_observation, target_observation = source.latest_observation, target.latest_observation
            if source_observation is None or target_observation is None:
                continue
            relations.setdefault(id(relation_recipe), (relation_recipe, []))[1].append(
                (relation, source, source_observation, target, target_observation)
            )
        for relation_recipe, relation_rows in relations.values():
            relation_recipe.render_rows(relation_rows, context, output, diagnostics)

    def _route(self, classification: Classification | None) -> CompiledSceneRenderRecipe:
        if classification is None:
            return self.fallback_recipes["unclassified"]
        return self.classification_recipes.get(classification.type, self.fallback_recipes["classified"])

    def recipe_for_entity(self, entity: Entity) -> CompiledSceneRenderRecipe | None:
        """Return the selected recipe, or None for an entity without observations."""
        observation = entity.latest_observation
        return None if observation is None else self._route(observation.primary_classification)


MAX_VISIBILITY_VARIANTS = 8
"""Variants each revision keeps compiled; views keep their own active variant, so eviction never changes output."""


@dataclass(frozen=True, slots=True)
class SceneRenderCatalogRevision:
    """A source document and its compiled catalog from one file read; consumers must not mutate the document."""

    document: CatalogJsonDocument
    catalog: SceneRenderCatalog
    _variants: dict[frozenset[OverlayFeature], SceneRenderCatalog] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def specialize(self, visibility: OverlayVisibility) -> SceneRenderCatalog:
        """Return the catalog without the features *visibility* hides, keeping the most recent variants compiled."""
        disabled = visibility.disabled & self.catalog.supported_features
        if not disabled:
            return self.catalog
        variant = self._variants.pop(disabled, None)
        if variant is None:
            identity = hashlib.sha256(f"{self.catalog.semantic_hash}:{','.join(sorted(disabled))}".encode()).hexdigest()
            compiled = _compile_document(
                self.document, self.catalog.content_hash, RenderProgramCompiler(disabled), identity[:16]
            )
            variant = replace(compiled, supported_features=self.catalog.supported_features)
        # Dicts keep insertion order: reinserting marks this variant most recent, and the first one is the oldest.
        self._variants[disabled] = variant
        if len(self._variants) > MAX_VISIBILITY_VARIANTS:
            del self._variants[next(iter(self._variants))]
        return variant


class SceneRenderCatalogLoader:
    """Parse, validate and compile documents through one production boundary."""

    def load_path(self, catalog_path: Path) -> SceneRenderCatalog:
        """Load and validate a bounded JSON file with duplicate-key detection."""
        return self.load_revision(catalog_path).catalog

    def load_revision(self, catalog_path: Path) -> SceneRenderCatalogRevision:
        """Read once and return the source document together with the catalog compiled from it."""
        document = self.read_document(catalog_path)
        return self.validate_revision(document)

    def validate_revision(self, document: CatalogJsonDocument) -> SceneRenderCatalogRevision:
        """Validate a source document and keep it with its catalog for visibility specialization."""
        return SceneRenderCatalogRevision(document, self.validate_document(document))

    def validate_document(self, document: CatalogJsonDocument) -> SceneRenderCatalog:
        """Validate all language semantics and return an immutable executable catalog.

        The structural JSON Schema pass is skipped for unmodified built-in catalogs.
        """
        _check_limits(document)
        metadata = _object(document.get("metadata"), "$.metadata")
        _keys(document, {"metadata", "templates", "recipes"}, {"metadata", "templates", "recipes"}, "$")
        _keys(
            metadata,
            {"id", "name", "schema_version", "description", "design_notes"},
            {"id", "name", "schema_version"},
            "$.metadata",
        )
        if type(metadata["schema_version"]) is not int or metadata["schema_version"] != 3:
            raise TemplateRuntimeError(
                "Unsupported Scene Render Catalog schema version; expected 3.",
                code="unsupported_version",
                location="$.metadata.schema_version",
            )
        for key in ("id", "name"):
            if not isinstance(metadata[key], str) or not metadata[key]:
                raise TemplateRuntimeError("Expected nonempty text.", location=f"$.metadata.{key}")
        if len(json.dumps(document, allow_nan=False).encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise TemplateRuntimeError("Catalog document size limit exceeded.", code="execution_limit")
        catalog_hash = _catalog_hash(document)
        if catalog_hash not in _built_in_catalog_hashes():
            try:
                Draft202012Validator(catalog_schema()).validate(document)
            except ValidationError as exc:
                raise TemplateRuntimeError(exc.message, code="invalid_structure", location=exc.json_path) from exc
        return _compile_document(document, catalog_hash, RenderProgramCompiler(), _semantic_hash(document))

    @staticmethod
    def read_document(path: Path) -> CatalogJsonDocument:
        """Read a bounded catalog JSON file, rejecting duplicate keys."""
        with path.open(encoding="utf-8") as stream:
            text = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise TemplateRuntimeError("Catalog document size limit exceeded.", code="execution_limit")
        try:
            value = json.loads(text, object_pairs_hook=_unique_object)
        except (RecursionError, json.JSONDecodeError) as exc:
            raise TemplateRuntimeError(str(exc), code="invalid_json") from exc
        return _object(value, "$")


BINDING_TYPES: Mapping[tuple[str, str], ValueType] = MappingProxyType(
    {
        ("bool", "value"): BOOL,
        ("number", "value"): NUMBER,
        ("color_classification_list", "highest_score"): COLOR_BINDING,
    }
)
"""The value a binding gives recipes, keyed by its ``(value_shape, picker)``; recipes see it as optional."""


def recipe_roots(group: str, kind: str, bindings: Mapping[str, ValueType]) -> dict[str, ValueType]:
    """Return the scopes a recipe of *group* and selector *kind* reads, with their value types.

    Relation recipes read the relation and its endpoints. Entity recipes read the Scene projection, their declared
    bindings; routing proves a classification exists for every recipe except ``unclassified``.
    """
    if group == "relations":
        return {"scene": RELATION_SCENE, "context": CONTEXT}
    scene = SCENE
    if kind != "unclassified":
        scene = replace(
            SCENE,
            fields=tuple(
                (name, value_type.present() if name == "classification" else value_type)
                for name, value_type in SCENE.fields
            ),
        )
    return {
        "scene": scene,
        "context": CONTEXT,
        "bindings": ValueType("bindings", tuple(bindings.items())),
    }


def _compile_bindings(raw: Mapping[str, object], location: str) -> Mapping[str, RecipeBinding]:
    bindings: dict[str, RecipeBinding] = {}
    for name, value in raw.items():
        binding = _object(value, f"{location}.bindings.{name}")
        _keys(
            binding,
            {"source", "attribute", "value_shape", "picker"},
            {"source", "attribute", "value_shape", "picker"},
            location,
        )
        kind = binding["value_shape"]
        value_type = BINDING_TYPES.get((cast(str, kind), cast(str, binding["picker"])))
        if (
            binding["source"] != "primary_classification_attribute"
            or value_type is None
            or not isinstance(binding["attribute"], str)
        ):
            raise TemplateRuntimeError("Unsupported typed attribute binding.", location=f"{location}.bindings.{name}")
        bindings[name] = RecipeBinding(binding["attribute"], cast(str, kind), value_type.optional())
    return MappingProxyType(bindings)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TemplateRuntimeError(f"Duplicate JSON key: {key}.", code="duplicate_definition")
        result[key] = value
    return result


def _check_limits(value: object, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise TemplateRuntimeError("Document depth limit exceeded.", code="execution_limit")
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION:
            raise TemplateRuntimeError("Record size limit exceeded.", code="execution_limit")
        for item in value.values():
            _check_limits(item, depth + 1)
    elif isinstance(value, (tuple, list)):
        if len(value) > MAX_COLLECTION:
            raise TemplateRuntimeError("Collection size limit exceeded.", code="execution_limit")
        for item in value:
            _check_limits(item, depth + 1)
    else:
        freeze(value)


def _catalog_hash(document: CatalogJsonDocument) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()[:16]


_DESCRIPTIVE_KEYS = frozenset({"description", "label"})


def _without_descriptions(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Drop descriptive text from a template, recipe or parameter, and from its steps."""
    result = {key: value for key, value in definition.items() if key not in _DESCRIPTIVE_KEYS}
    if isinstance(result.get("steps"), list):
        result["steps"] = [
            _without_descriptions(step) if isinstance(step, Mapping) else step for step in result["steps"]
        ]
    if isinstance(result.get("parameters"), Mapping):
        result["parameters"] = {
            name: _without_descriptions(parameter) if isinstance(parameter, Mapping) else parameter
            for name, parameter in result["parameters"].items()
        }
    return result


def _compile_document(
    document: CatalogJsonDocument, catalog_hash: str, compiler: RenderProgramCompiler, semantic_hash: str
) -> SceneRenderCatalog:
    metadata = _object(document["metadata"], "$.metadata")
    templates = compiler.compile_catalog(_object(document["templates"], "$.templates"))
    groups = _object(document["recipes"], "$.recipes")
    _keys(groups, {"fallbacks", "classifications", "relations"}, {"fallbacks", "classifications"}, "$.recipes")
    fallbacks: dict[str, CompiledSceneRenderRecipe] = {}
    classifications: dict[str, CompiledSceneRenderRecipe] = {}
    relations: dict[str, CompiledSceneRelationRenderRecipe] = {}
    features: set[OverlayFeature] = set()
    ids: set[str] = set()
    for group in ("fallbacks", "classifications", "relations"):
        for index, raw in enumerate(_list(groups.get(group, []), f"$.recipes.{group}")):
            location = f"$.recipes.{group}[{index}]"
            recipe = _object(raw, location)
            _keys(
                recipe,
                {"id", "selector", "enabled", "bindings", "values", "steps", "description", "label"},
                {"id", "selector", "steps"},
                location,
            )
            recipe_id = recipe["id"]
            if not isinstance(recipe_id, str) or not recipe_id or recipe_id in ids:
                raise TemplateRuntimeError(f"Invalid or duplicate recipe id: {recipe_id}.", location=f"{location}.id")
            ids.add(recipe_id)
            selector = _object(recipe["selector"], f"{location}.selector")
            _keys(
                selector,
                {"kind"} if group == "fallbacks" else {"kind", "types"},
                {"kind"} if group == "fallbacks" else {"kind", "types"},
                f"{location}.selector",
            )
            kind = selector["kind"]
            allowed = (
                ("classified", "unclassified")
                if group == "fallbacks"
                else ("classification",)
                if group == "classifications"
                else ("relation",)
            )
            if kind not in allowed:
                raise TemplateRuntimeError(f"Invalid selector kind for {group}.", location=f"{location}.selector.kind")
            bindings = _compile_bindings(_object(recipe.get("bindings", {}), f"{location}.bindings"), location)
            if group == "relations" and bindings:
                raise TemplateRuntimeError(
                    "Relation recipes cannot declare classification bindings.", location=location
                )
            roots = recipe_roots(
                group, cast(str, kind), {name: binding.value_type for name, binding in bindings.items()}
            )
            program = compiler.compile_program(
                {key: value for key, value in recipe.items() if key in {"enabled", "values", "steps", "description"}},
                roots=roots,
                location=location,
            )
            features.update(program.features)
            selectors = (kind,) if group == "fallbacks" else _list(selector["types"], location)
            if not selectors or any(not isinstance(item, str) or not item for item in selectors):
                raise TemplateRuntimeError(
                    "Selectors require nonempty type strings.", location=f"{location}.selector.types"
                )
            if group == "relations":
                relation_recipe = CompiledSceneRelationRenderRecipe(recipe_id, program)
                for selected in cast(Sequence[str], selectors):
                    if selected in relations:
                        raise TemplateRuntimeError(
                            f"Duplicate relation selector: {selected}.", location=f"{location}.selector.types"
                        )
                    relations[selected] = relation_recipe
            else:
                destination = fallbacks if group == "fallbacks" else classifications
                entity_recipe = CompiledSceneRenderRecipe(recipe_id, program, bindings)
                for selected in cast(Sequence[str], selectors):
                    if selected in destination:
                        raise TemplateRuntimeError(
                            f"Duplicate {group} selector: {selected}.", location=f"{location}.selector.types"
                        )
                    destination[selected] = entity_recipe
    if set(fallbacks) != {"classified", "unclassified"}:
        raise TemplateRuntimeError(
            "Missing required classified/unclassified fallback recipes.", location="$.recipes.fallbacks"
        )
    return SceneRenderCatalog(
        cast(str, metadata["id"]),
        catalog_hash,
        templates,
        fallbacks,
        classifications,
        relations,
        semantic_hash,
        frozenset(features),
    )


def _semantic_hash(document: CatalogJsonDocument) -> str:
    templates = {name: _without_descriptions(definition) for name, definition in document["templates"].items()}
    recipes = {name: [_without_descriptions(recipe) for recipe in items] for name, items in document["recipes"].items()}
    return _catalog_hash(
        {"schema_version": 3, "compiler_revision": COMPILER_REVISION, "templates": templates, "recipes": recipes}
    )


@cache
def _built_in_catalog_hashes() -> frozenset[str]:
    """Hash the built-in catalogs, whose schema conformance the test suite enforces."""
    return frozenset(_catalog_hash(SceneRenderCatalogLoader.read_document(path)) for path in BUILT_IN_CATALOG_PATHS)


@cache
def get_built_in_scene_render_catalog() -> SceneRenderCatalog:
    """Return the default built-in catalog, compiled once per process, for consumers without a selection."""
    return SceneRenderCatalogLoader().load_path(BUILT_IN_CATALOG_PATH)
