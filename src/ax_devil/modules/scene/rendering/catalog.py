"""Catalog-owned scene routing and per-recipe row-column execution of compiled recipes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from types import MappingProxyType

import numpy as np

from ax_devil.modules.scene.model import Classification, Entity, EntityRelation, Observation, Scene
from ax_devil.modules.scene.rendering.catalog_tables import EntityTable, RecipeBinding, RelationTable, Table
from ax_devil.modules.scene.rendering.template_runtime.definitions import COMPILER_REVISION
from ax_devil.modules.scene.rendering.template_runtime.emission import emit
from ax_devil.modules.scene.rendering.template_runtime.kernels import RowState
from ax_devil.modules.scene.rendering.template_runtime.program import RenderCatalog, RenderProgram
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.scene.rendering.visibility import OverlayFeature
from ax_devil.modules.video_player.engine.drawing import DrawingTarget
from ax_devil.modules.video_player.engine.render_context import RenderContext

BUILT_IN_CATALOG_PATHS = tuple(
    Path(__file__).with_name("catalog_definitions") / f"{name}.json"
    for name in ("standard", "minimal", "chunky", "glass", "tracking", "classic")
)
"""The catalogs that come with the app, in the order they are listed. They are read in place, never copied into the
user catalog store and never written."""
BUILT_IN_CATALOG_PATH = BUILT_IN_CATALOG_PATHS[0]
"""The built-in catalog new views start from until the user chooses another, and the fallback when that one fails."""


def _run(
    update: Callable[..., object],
    table: Table,
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
        _run(self._update, EntityTable(rows, self.bindings), context, output, self.recipe_id, diagnostics)


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
        _run(self._update, RelationTable(rows), context, output, self.recipe_id, diagnostics)


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
