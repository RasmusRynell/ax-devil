"""Authoring fields: descriptive labels and the step visible flag."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalogLoader
from ax_devil.modules.scene.rendering.template_runtime.compiler import RenderProgramCompiler
from ax_devil.modules.scene.rendering.template_runtime.values import TemplateRuntimeError
from ax_devil.modules.video_player.engine.render_context import RenderContext
from tests.catalog_helpers import CLASSIC_CATALOG_PATH
from tests.drawing_helpers import record_template

CONTEXT = RenderContext.create(800, 400)


def _document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CLASSIC_CATALOG_PATH.read_text(encoding="utf-8"))
    return document


def _point_step(**extra: object) -> dict[str, object]:
    return {"primitive": "point", "fields": {"position": {"x": 0.5, "y": 0.5}}, **extra}


def test_hidden_steps_are_validated_but_draw_nothing() -> None:
    catalog = RenderProgramCompiler().compile_catalog(
        {"main": {"steps": [_point_step(label="Shown"), _point_step(label="Hidden", visible=False)]}}
    )

    assert len(record_template(catalog, "main", {}, context=CONTEXT)) == 1
    with pytest.raises(TemplateRuntimeError, match="Unknown image_point fields"):
        RenderProgramCompiler().compile_catalog(
            {"main": {"steps": [{**_point_step(visible=False), "fields": {"position": {"x": 0, "z": 0}}}]}}
        )


def test_visible_must_be_a_boolean() -> None:
    with pytest.raises(TemplateRuntimeError) as error:
        RenderProgramCompiler().compile_catalog({"main": {"steps": [_point_step(visible="no")]}})

    assert error.value.diagnostic.location == "$.templates.main.steps[0].visible"


def test_labels_do_not_change_rendering_identity() -> None:
    document = _document()
    relabeled = copy.deepcopy(document)
    relabeled["recipes"]["classifications"][0]["label"] = "Renamed"
    relabeled["recipes"]["classifications"][0]["steps"][0]["label"] = "Renamed part"
    relabeled["templates"]["object_label"]["parameters"]["id_font_size"]["label"] = "Renamed setting"
    loader = SceneRenderCatalogLoader()

    assert (
        loader.validate_document(relabeled).rendering_identity == loader.validate_document(document).rendering_identity
    )


def test_hiding_a_step_changes_rendering_identity() -> None:
    document = _document()
    hidden = copy.deepcopy(document)
    hidden["recipes"]["classifications"][0]["steps"][1]["visible"] = False
    loader = SceneRenderCatalogLoader()

    assert loader.validate_document(hidden).rendering_identity != loader.validate_document(document).rendering_identity


def test_built_in_catalog_labels_every_recipe_step_and_parameter() -> None:
    document = _document()

    for template in document["templates"].values():
        assert template["label"]
        assert all(parameter["label"] for parameter in template["parameters"].values())
        assert all(step["label"] for step in template["steps"])
    for recipes in document["recipes"].values():
        for recipe in recipes:
            assert recipe["label"] and recipe["description"]
            assert all(step["label"] for step in recipe["steps"])
