"""Validated catalog loading: parse, validate, compile and specialize catalog documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ax_devil.modules.scene.rendering.catalog import (
    BUILT_IN_CATALOG_PATH,
    BUILT_IN_CATALOG_PATHS,
    CompiledSceneRelationRenderRecipe,
    CompiledSceneRenderRecipe,
    SceneRenderCatalog,
)
from ax_devil.modules.scene.rendering.catalog_tables import RecipeBinding
from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
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
from ax_devil.modules.scene.rendering.template_runtime.schema import catalog_schema
from ax_devil.modules.scene.rendering.template_runtime.structure import require_keys, require_list, require_object
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError, freeze
from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility

CatalogJsonDocument = Mapping[str, Any]


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
        metadata = require_object(document.get("metadata"), "$.metadata")
        require_keys(document, {"metadata", "templates", "recipes"}, {"metadata", "templates", "recipes"}, "$")
        require_keys(
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
        return require_object(value, "$")


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
        binding = require_object(value, f"{location}.bindings.{name}")
        require_keys(
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
    metadata = require_object(document["metadata"], "$.metadata")
    templates = compiler.compile_catalog(require_object(document["templates"], "$.templates"))
    groups = require_object(document["recipes"], "$.recipes")
    require_keys(groups, {"fallbacks", "classifications", "relations"}, {"fallbacks", "classifications"}, "$.recipes")
    fallbacks: dict[str, CompiledSceneRenderRecipe] = {}
    classifications: dict[str, CompiledSceneRenderRecipe] = {}
    relations: dict[str, CompiledSceneRelationRenderRecipe] = {}
    features: set[OverlayFeature] = set()
    ids: set[str] = set()
    for group in ("fallbacks", "classifications", "relations"):
        for index, raw in enumerate(require_list(groups.get(group, []), f"$.recipes.{group}")):
            location = f"$.recipes.{group}[{index}]"
            recipe = require_object(raw, location)
            require_keys(
                recipe,
                {"id", "selector", "enabled", "bindings", "values", "steps", "description", "label"},
                {"id", "selector", "steps"},
                location,
            )
            recipe_id = recipe["id"]
            if not isinstance(recipe_id, str) or not recipe_id or recipe_id in ids:
                raise TemplateRuntimeError(f"Invalid or duplicate recipe id: {recipe_id}.", location=f"{location}.id")
            ids.add(recipe_id)
            selector = require_object(recipe["selector"], f"{location}.selector")
            require_keys(
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
            bindings = _compile_bindings(require_object(recipe.get("bindings", {}), f"{location}.bindings"), location)
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
            selectors = (kind,) if group == "fallbacks" else require_list(selector["types"], location)
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
