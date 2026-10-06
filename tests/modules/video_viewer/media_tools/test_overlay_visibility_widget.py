"""Visibility controls follow catalog availability and retain per-view preferences."""

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.scene.rendering import OverlayFeature, SceneRenderCatalogManager
from ax_devil.modules.scene.rendering.catalog import BUILT_IN_CATALOG_PATHS
from ax_devil.modules.video_viewer.media_tools.overlay_visibility_widget import OverlayVisibilityWidget


def test_controls_retain_unsupported_choices_and_reset(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager
) -> None:
    selection = render_catalog_manager.create_selection()
    widget = OverlayVisibilityWidget(selection)
    qtbot.addWidget(widget)
    confidence = widget.findChild(QCheckBox, "overlayFeature_confidence")
    reset = widget.findChild(QPushButton, "overlayVisibilityReset")
    assert confidence is not None and reset is not None
    confidence.setChecked(False)
    assert OverlayFeature.CONFIDENCE in selection.visibility.disabled
    minimal: Path = next(path for path in BUILT_IN_CATALOG_PATHS if path.stem == "minimal")
    selection.select_catalog(minimal)
    assert not confidence.isEnabled() and not confidence.isChecked()
    selection.select_catalog(render_catalog_manager.built_in_catalog_path())
    assert confidence.isEnabled() and not confidence.isChecked()
    reset.click()
    assert confidence.isChecked() and not reset.isEnabled()
    widget.cleanup()
    widget.cleanup()
    selection.set_feature_enabled(OverlayFeature.CONFIDENCE, False)
    assert confidence.isChecked()  # Closed controls no longer observe the selection.
