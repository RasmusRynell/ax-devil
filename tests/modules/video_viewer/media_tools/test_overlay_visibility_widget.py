"""Visibility controls follow catalog availability and retain per-view preferences."""

from pathlib import Path

from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import OverlayFeature, SceneRenderCatalogManager, SceneRenderCatalogSelection
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS
from ax_devil.modules.video_viewer.media_tools.overlay_visibility_widget import OverlayVisibilityWidget
from tests.catalog_helpers import wait_for_catalogs


def _visibility_widget(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> tuple[OverlayVisibilityWidget, SceneRenderCatalogSelection, QPushButton, QPushButton]:
    selection = render_catalog_manager.create_selection()
    widget = OverlayVisibilityWidget(selection)
    qtbot.addWidget(widget)
    confidence = widget.findChild(QPushButton, "overlayFeature_confidence")
    reset = widget.findChild(QPushButton, "overlayVisibilityReset")
    assert confidence is not None and reset is not None
    return widget, selection, confidence, reset


def test_controls_retain_unsupported_choices_and_reset(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    _, selection, confidence, reset = _visibility_widget(qtbot, render_catalog_manager)
    confidence.setChecked(False)
    assert OverlayFeature.CONFIDENCE in selection.visibility.disabled
    minimal: Path = next(path for path in BUILT_IN_CATALOG_PATHS if path.stem == "minimal")
    selection.select_catalog(minimal)
    wait_for_catalogs(qtbot, render_catalog_manager)
    assert not confidence.isEnabled() and not confidence.isChecked()
    selection.select_catalog(render_catalog_manager.built_in_catalog_path())
    wait_for_catalogs(qtbot, render_catalog_manager)
    assert confidence.isEnabled() and not confidence.isChecked()
    reset.click()
    assert confidence.isChecked() and not reset.isEnabled()


def test_cleaned_up_controls_stop_observing_the_selection(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    widget, selection, confidence, _ = _visibility_widget(qtbot, render_catalog_manager)
    widget.cleanup()
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    assert confidence.isChecked()
