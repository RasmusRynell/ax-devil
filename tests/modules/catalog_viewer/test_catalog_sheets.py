"""Example sheets: every type gets its own sheet, examples reach the intended type, and the built-in draws cleanly."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.catalog_viewer.sheets import Caption, SheetDrawing, drawing_errors, sheets
from ax_devil.modules.scene.model import Attribute, Entity, Scene, TimeSlice
from ax_devil.modules.scene.rendering import SceneRenderCatalogLoader, get_built_in_scene_render_catalog
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATH
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer, DrawingSettings
from ax_devil.modules.video_player.engine.render_context import RenderContext

OBJECT_GROUPS = ("classifications", "fallbacks")


def _built_in() -> Any:
    return SceneRenderCatalogLoader.read_document(BUILT_IN_CATALOG_PATH)


def _ids(document: Any, groups: tuple[str, ...]) -> list[str]:
    return [recipe["id"] for group in groups for recipe in document["recipes"][group]]


def _attributes(entity: Entity) -> list[Attribute]:
    return [
        attribute
        for observation in entity.observations
        for item in observation.classification
        for attribute in item.attributes
    ]


def _routed(entity: Entity) -> str:
    recipe = get_built_in_scene_render_catalog().recipe_for_entity(entity)
    assert recipe is not None
    return recipe.recipe_id


def test_there_is_an_overview_a_street_a_sheet_per_type_and_relation_and_a_crowd() -> None:
    document = _built_in()

    keys = [sheet.key for sheet in sheets(document)]

    assert keys == ["overview", "street", *_ids(document, (*OBJECT_GROUPS, "relations")), "crowd"]


def test_each_type_sheet_draws_only_that_type() -> None:
    document = _built_in()
    object_ids = set(_ids(document, OBJECT_GROUPS))

    for sheet in sheets(document):
        if sheet.key in object_ids:
            assert {_routed(entity) for entity in sheet.scene.entities.values()} == {sheet.key}


def test_the_overview_starts_with_one_object_of_every_type_then_every_relation() -> None:
    document = _built_in()
    object_ids = _ids(document, OBJECT_GROUPS)
    overview = sheets(document)[0]
    entities = list(overview.scene.entities.values())

    assert [_routed(entity) for entity in entities[: len(object_ids)]] == object_ids
    assert len(overview.scene.relations) == len(document["recipes"]["relations"])


def test_the_built_in_catalog_draws_every_sheet_without_errors(qtbot: QtBot) -> None:
    catalog = get_built_in_scene_render_catalog()

    assert {sheet.key: drawing_errors(sheet, catalog) for sheet in sheets(_built_in())} == {
        sheet.key: () for sheet in sheets(_built_in())
    }


@pytest.mark.parametrize("width,visible", [(120, 0), (800, 1)])
def test_caption_plates_skip_empty_lines_at_small_sizes(qtbot: QtBot, width: int, visible: int) -> None:
    """A caption too narrow even for an ellipsis has neither a plate nor text."""
    sheet = replace(
        sheets(_built_in())[0],
        scene=Scene(time_slice=TimeSlice(start=0, end=1)),
        captions=(Caption(0.1, 0.1, "Visible when wide", width=0.1), Caption(0.5, 0.1, "", width=0.1)),
    )
    context = RenderContext.create(width, max(1, width // 2))
    drawing = SheetDrawing(sheet, get_built_in_scene_render_catalog())
    result = drawing.prepare(context, DrawingBuffer(DrawingSettings.for_context(context)))
    assert len(result.paths) == len(result.texts) == visible
    assert drawing.diagnostics == ()


def test_every_example_keeps_its_own_id_and_ids_come_in_the_usual_formats() -> None:
    document = _built_in()
    built = {sheet.key: sheet for sheet in sheets(document)}
    relations = len(document["recipes"]["relations"])

    # Entities are keyed by id, so a repeated id would replace an example and leave fewer than were placed.
    assert len(built["overview"].scene.entities) == len(_ids(document, OBJECT_GROUPS)) + 2 * relations
    ids = [entity_id for sheet in built.values() for entity_id in sheet.scene.entities]
    assert sum(len(entity_id) == 36 for entity_id in ids) > len(ids) / 4
    assert any(entity_id.isdigit() for entity_id in ids)
    assert any(not entity_id.isdigit() and len(entity_id) < 36 for entity_id in ids)


def test_sheets_are_the_same_every_time() -> None:
    def summary() -> list[tuple[str, list[tuple[str, list[Attribute]]]]]:
        return [
            (sheet.key, [(entity.id, _attributes(entity)) for entity in sheet.scene.entities.values()])
            for sheet in sheets(_built_in())
        ]

    assert summary() == summary()


def test_examples_carry_plausible_varied_attributes() -> None:
    human = next(sheet for sheet in sheets(_built_in()) if sheet.key == "human")
    values = [
        {attribute.name: attribute.value for attribute in _attributes(entity)}
        for entity in human.scene.entities.values()
    ]

    bags = [value["carries_bag"] for value in values]
    uppers = {value["upper_clothing_colors"][0].name for value in values}
    scores = [color.score.value for value in values for color in value["lower_clothing_colors"]]
    assert 0 < sum(bags) < len(bags) and bags != [index % 2 == 0 for index in range(len(bags))]
    assert len(uppers) >= 4
    assert all(0 < score < 1 for score in scores)


def test_the_street_shows_people_with_their_heads_and_traffic_routed_by_class() -> None:
    document = _built_in()
    street = next(sheet for sheet in sheets(document) if sheet.key == "street")
    routed = [_routed(entity) for entity in street.scene.entities.values()]

    assert {"human", "head", "vehicle", "motion"} <= set(routed)
    assert {relation.type for relation in street.scene.relations} == {"has_part"}
    assert len(street.scene.relations) == routed.count("head")


def test_the_street_falls_back_to_the_catalogs_general_types_when_it_has_few_of_its_own() -> None:
    document = _built_in()
    document["recipes"]["classifications"] = [document["recipes"]["classifications"][0]]
    document["recipes"]["relations"] = []
    catalog = SceneRenderCatalogLoader().validate_document(document)
    street = next(sheet for sheet in sheets(document) if sheet.key == "street")

    routed = [catalog.recipe_for_entity(entity) for entity in street.scene.entities.values()]

    assert {recipe.recipe_id if recipe is not None else None for recipe in routed} == {
        "vehicle",
        "generic_classified",
        "motion",
    }
    assert not street.scene.relations
    assert drawing_errors(street, catalog) == ()


def test_other_classes_get_an_example_even_when_every_usual_name_has_its_own_type() -> None:
    document = _built_in()
    names = ["license_plate", "animal", "unknown", "example"]
    document["recipes"]["classifications"][0]["selector"]["types"].extend(names)
    catalog = SceneRenderCatalogLoader().validate_document(document)
    other = next(sheet for sheet in sheets(document) if sheet.key == "generic_classified")

    recipes = [catalog.recipe_for_entity(entity) for entity in other.scene.entities.values()]

    assert {recipe.recipe_id for recipe in recipes if recipe is not None} == {"generic_classified"}
    assert None not in recipes
