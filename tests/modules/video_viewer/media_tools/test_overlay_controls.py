from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QSpinBox,
    QToolButton,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel, OverlayPersistenceControls
from ax_devil.modules.video_viewer.media_tools.appearance_popover import AppearancePopover
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from tests.catalog_helpers import create_catalog


def test_controls_emit_settings_changes(qtbot: QtBot) -> None:
    controls = OverlayPersistenceControls(
        initial_settings=OverlayPersistenceSettings(enabled=False, timeout_ms=None, opacity=0.6)
    )
    qtbot.addWidget(controls)

    enabled_checkbox = controls.findChild(QCheckBox, "overlayEnabledCheckbox")
    assert enabled_checkbox is not None

    with qtbot.waitSignal(controls.settingsChanged) as blocker:
        enabled_checkbox.setChecked(True)

    emitted: OverlayPersistenceSettings = blocker.args[0]
    assert emitted.enabled is True
    assert emitted.timeout_ms is None
    assert pytest.approx(emitted.opacity) == 0.6

    timeout_controls = OverlayPersistenceControls(
        initial_settings=OverlayPersistenceSettings(enabled=True, timeout_ms=1500, opacity=0.5)
    )
    qtbot.addWidget(timeout_controls)

    timeout_spin = timeout_controls.findChild(QSpinBox, "overlayTimeoutSpin")
    assert timeout_spin is not None

    with qtbot.waitSignal(timeout_controls.settingsChanged) as blocker:
        timeout_spin.setValue(2500)

    timeout_emitted: OverlayPersistenceSettings = blocker.args[0]
    assert timeout_emitted.enabled is True
    assert timeout_emitted.timeout_ms == 2500
    assert pytest.approx(timeout_emitted.opacity) == 0.5

    disable_timeout_controls = OverlayPersistenceControls(
        initial_settings=OverlayPersistenceSettings(enabled=True, timeout_ms=2000, opacity=0.4)
    )
    qtbot.addWidget(disable_timeout_controls)

    timeout_checkbox = disable_timeout_controls.findChild(QCheckBox, "overlayTimeoutCheckbox")
    assert timeout_checkbox is not None

    with qtbot.waitSignal(disable_timeout_controls.settingsChanged) as blocker:
        timeout_checkbox.setChecked(False)

    disabled_emitted: OverlayPersistenceSettings = blocker.args[0]
    assert disabled_emitted.enabled is True
    assert disabled_emitted.timeout_ms is None
    assert pytest.approx(disabled_emitted.opacity) == 0.4


def test_media_tools_panel_reemits_persistence_changes(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    initial = OverlayPersistenceSettings(enabled=True, timeout_ms=1800, opacity=0.8)
    panel = MediaToolsPanel(
        filter_model=SessionFilter(None),
        overlay_settings=initial,
        render_catalog_selection=render_catalog_selection,
    )
    qtbot.addWidget(panel)

    opacity_spin = panel.overlay_controls.findChild(QDoubleSpinBox, "overlayOpacitySpin")
    assert opacity_spin is not None

    with qtbot.waitSignal(panel.overlayPersistenceChanged) as blocker:
        opacity_spin.setValue(0.55)

    emitted: OverlayPersistenceSettings = blocker.args[0]
    assert pytest.approx(emitted.opacity) == 0.55
    assert emitted.enabled is True


def test_appearance_popover_changes_the_catalog_and_closes_for_the_catalog_viewer(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, small_catalog_path: Path
) -> None:
    catalog = create_catalog(qtbot, render_catalog_selection.manager, "Review", base_catalog_path=small_catalog_path)
    panel = MediaToolsPanel(render_catalog_selection=render_catalog_selection, filter_model=SessionFilter())
    qtbot.addWidget(panel)
    panel.show()
    button = panel.findChild(QToolButton, "mediaToolsAppearanceButton")
    popover = panel.findChild(AppearancePopover)
    assert button is not None and popover is not None
    button.click()
    combo = popover.findChild(QComboBox, "renderCatalogCombo")
    open_button = popover.findChild(QPushButton, "openRenderCatalogsButton")
    assert popover.isVisible() and combo is not None and open_button is not None

    with qtbot.waitSignal(render_catalog_selection.activeCatalogChanged):
        combo.setCurrentIndex(combo.findData(str(catalog.path)))
    assert render_catalog_selection.active_catalog_path() == catalog.path
    with qtbot.waitSignal(panel.catalogViewerRequested):
        open_button.click()
    assert not popover.isVisible()
