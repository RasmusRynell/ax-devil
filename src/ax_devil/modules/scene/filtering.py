"""Scene filtering helpers shared by rendering and viewer tools."""

from __future__ import annotations

from dataclasses import replace

from ax_devil.modules.filtering.filter_config import FilterConfig, FilterOption, FilterState, iter_enabled_options
from ax_devil.modules.scene.model import Entity, EntityId, Scene
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


def _entity_matches_options(
    entity: Entity,
    options: tuple[FilterOption, ...],
    state: FilterState,
) -> bool:
    """Return whether any enabled option keeps the entity."""
    for option in options:
        try:
            if option.predicate(entity, state):
                return True
        except Exception:  # pragma: no cover - defensive guard around decoder predicates
            logger.exception(f"Filter predicate '{option.id}' raised an exception for entity '{entity.id}'")
    return False


def _enabled_options(config: FilterConfig, state: FilterState) -> tuple[FilterOption, ...]:
    enabled = tuple(iter_enabled_options(config, state))
    if not enabled:
        logger.debug("All filter options disabled; resulting scene will have no entities.")
    return enabled


def matches_id_query(entity_id: str, state: FilterState) -> bool:
    """Return whether *entity_id* contains the state's id query, ignoring case."""
    return _contains_query(entity_id, state.id_query.casefold())


def filter_scene(scene: Scene, config: FilterConfig, state: FilterState) -> Scene:
    """Apply filter state to a scene and return a filtered copy.

    Entities whose id does not contain ``state.id_query`` (case-insensitive) are always dropped.
    """
    if not scene.entities:
        return scene

    enabled_options = _enabled_options(config, state)
    if not enabled_options:
        return replace(scene, entities={})

    id_query = state.id_query.casefold()
    filtered_entities: dict[EntityId, Entity] = {
        entity_id: entity
        for entity_id, entity in scene.entities.items()
        if _contains_query(entity_id, id_query) and _keeps(entity, enabled_options, config, state)
    }
    return replace(scene, entities=filtered_entities)


def _contains_query(entity_id: str, folded_query: str) -> bool:
    return folded_query in entity_id.casefold()


def _keeps(
    entity: Entity,
    enabled_options: tuple[FilterOption, ...],
    config: FilterConfig,
    state: FilterState,
) -> bool:
    if not enabled_options:
        return False
    if _entity_matches_options(entity, enabled_options, state):
        return True
    if _entity_matches_options(entity, config.options, state):
        return False
    logger.debug(f"Entity {entity.id} not covered by any filter option; keeping by default")
    return True


def process_scene(scene: Scene, config: FilterConfig, state: FilterState) -> Scene:
    """Return the filtered scene for the supplied filter configuration and state."""
    return filter_scene(scene, config, state)
