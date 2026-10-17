from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QMenu,
    QSpinBox,
    QToolButton,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel, OverlayPersistenceControls
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


def test_media_tools_panel_open_catalogs_button_requests_catalog_management(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    panel = MediaToolsPanel(render_catalog_selection=render_catalog_selection, filter_model=SessionFilter())
    qtbot.addWidget(panel)
    open_button = panel.findChild(QToolButton, "openRenderCatalogsButton")
    assert open_button is not None

    with qtbot.waitSignal(panel.catalogViewerRequested):
        open_button.click()


def _submenu(panel: MediaToolsPanel, title: str) -> QMenu:
    """Return the submenu of the panel's overflow menu whose title starts with *title*."""
    more_button = panel.findChild(QToolButton, "mediaToolsMoreButton")
    assert more_button is not None and more_button.menu() is not None
    (action,) = [action for action in more_button.menu().actions() if action.text().startswith(title)]
    submenu = action.menu()
    assert isinstance(submenu, QMenu)
    return submenu


@pytest.mark.parametrize("title", ["Overlay filter", "Catalog"])
def test_media_tools_menu_allows_changing_filters_and_catalog(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, small_catalog_path: Path, title: str
) -> None:
    """The overflow menu's filter and catalog submenus expose working controls."""
    catalog = create_catalog(qtbot, render_catalog_selection.manager, "Review", base_catalog_path=small_catalog_path)
    panel = MediaToolsPanel(
        render_catalog_selection=render_catalog_selection, filter_model=SessionFilter(build_default_filter_config())
    )
    qtbot.addWidget(panel)
    panel.show()
    submenu = _submenu(panel, title)
    submenu.popup(panel.mapToGlobal(QPoint(0, 0)))
    assert submenu.isVisible()
    if title == "Overlay filter":
        checkbox = panel.filter_widget.findChild(QCheckBox)
        assert checkbox is not None and checkbox.isVisible()
        with qtbot.waitSignal(panel.filter_model.changed):
            QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
    else:
        combo = panel.catalog_selector.findChild(QComboBox, "renderCatalogCombo")
        assert combo is not None and combo.isVisible()
        with qtbot.waitSignal(render_catalog_selection.activeCatalogChanged):
            combo.setCurrentIndex(combo.findData(str(catalog.path)))
        assert render_catalog_selection.active_catalog_path() == catalog.path
    submenu.close()


def test_media_tools_panel_filter_menu_tracks_filter_state(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    panel = MediaToolsPanel(
        render_catalog_selection=render_catalog_selection, filter_model=SessionFilter(build_default_filter_config())
    )
    qtbot.addWidget(panel)
    submenu = _submenu(panel, "Overlay filter")
    assert submenu.title() == "Overlay filter"

    total = len(panel.filter_model.options)
    checkbox = panel.filter_widget.findChild(QCheckBox)
    assert checkbox is not None
    checkbox.setChecked(False)
    assert submenu.title() == f"Overlay filter {total - 1}/{total}"
