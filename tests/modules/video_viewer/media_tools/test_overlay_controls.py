from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QToolButton,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering import build_default_filter_config
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Entity, EntityId, Scene, TimeSlice
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_viewer.media_tools import (
    MediaToolsPanel,
    MediaToolsSection,
    OverlayPersistenceControls,
)
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings


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

    assert panel.filter_widget is not None

    opacity_spin = panel.overlay_controls.findChild(QDoubleSpinBox, "overlayOpacitySpin")
    assert opacity_spin is not None

    with qtbot.waitSignal(panel.overlayPersistenceChanged) as blocker:
        opacity_spin.setValue(0.55)

    emitted: OverlayPersistenceSettings = blocker.args[0]
    assert pytest.approx(emitted.opacity) == 0.55
    assert emitted.enabled is True


def test_media_tools_panel_reemits_catalog_management_request(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    panel = MediaToolsPanel(render_catalog_selection=render_catalog_selection, filter_model=SessionFilter())
    qtbot.addWidget(panel)

    with qtbot.waitSignal(panel.catalogViewerRequested):
        panel.catalog_selector.catalogViewerRequested.emit()


@pytest.mark.parametrize("button_name", ["entityFilterButton", "renderCatalogButton"])
def test_media_tools_popups_allow_changing_filters_and_catalog(
    qtbot: QtBot, render_catalog_selection: SceneRenderCatalogSelection, small_catalog_path: Path, button_name: str
) -> None:
    """The visible toolbar buttons expose working filter and catalog controls."""
    catalog = render_catalog_selection.manager.create_catalog("Review", base_catalog_path=small_catalog_path)
    panel = MediaToolsPanel(
        render_catalog_selection=render_catalog_selection, filter_model=SessionFilter(build_default_filter_config())
    )
    qtbot.addWidget(panel)
    panel.show()
    button = panel.findChild(QToolButton, button_name)
    assert button is not None and button.isVisible()
    menu = button.menu()
    assert menu is not None
    changed: list[bool] = []

    def interact() -> None:
        try:
            assert menu.isVisible()
            if button_name == "entityFilterButton":
                checkbox = panel.filter_widget.findChild(QCheckBox)
                assert checkbox is not None and checkbox.isVisible()
                QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
            else:
                combo = panel.catalog_selector.findChild(QComboBox, "renderCatalogCombo")
                assert combo is not None and combo.isVisible()
                combo.setCurrentIndex(combo.findData(str(catalog.path)))
            changed.append(True)
        finally:
            menu.close()

    signal = (
        panel.filter_model.changed
        if button_name == "entityFilterButton"
        else render_catalog_selection.activeCatalogChanged
    )
    with qtbot.waitSignal(signal):
        # QToolButton's popup runs a nested event loop; interact once that menu opens.
        QTimer.singleShot(0, interact)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert changed == [True]
    if button_name == "renderCatalogButton":
        assert render_catalog_selection.active_catalog_path() == catalog.path


def test_media_tools_panel_id_search_and_filter_button_track_filter_state(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    panel = MediaToolsPanel(
        render_catalog_selection=render_catalog_selection, filter_model=SessionFilter(build_default_filter_config())
    )
    qtbot.addWidget(panel)
    search = panel.findChild(QLineEdit, "entityIdSearch")
    button = panel.findChild(QToolButton, "entityFilterButton")
    assert search is not None and button is not None
    scene = Scene(time_slice=TimeSlice(start=0, end=0))
    for entity_id in ("Track-12", "track-34"):
        scene.add_entity(Entity(id=EntityId(entity_id)))

    with qtbot.waitSignal(panel.filter_model.changed):
        search.setText(" K-1 ")
    assert list(panel.filter_model.process_scene(scene).entities) == [EntityId("Track-12")]

    total = len(panel.filter_model.options)
    checkbox = panel.filter_widget.findChild(QCheckBox)
    assert checkbox is not None
    checkbox.setChecked(False)
    assert button.text() == f"Filter {total - 1}/{total}"


def test_media_tools_panel_export_button_emits_request(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
) -> None:
    panel = MediaToolsPanel(
        show_export=True, render_catalog_selection=render_catalog_selection, filter_model=SessionFilter()
    )
    qtbot.addWidget(panel)
    button = panel.findChild(QToolButton, "exportVideoButton")
    assert button is not None

    with qtbot.waitSignal(panel.exportRequested):
        button.click()


@pytest.mark.parametrize("dismiss_key", [Qt.Key.Key_Escape, None])
def test_section_help_stays_open_on_mouse_move(
    qtbot: QtBot,
    render_catalog_selection: SceneRenderCatalogSelection,
    dismiss_key: Qt.Key | None,
) -> None:
    """Clicked help survives pointer movement and dismisses with Escape or a click."""
    panel = MediaToolsPanel(render_catalog_selection=render_catalog_selection, filter_model=SessionFilter())
    qtbot.addWidget(panel)
    panel.show()
    tabs = panel.findChild(QTabWidget, "mediaToolsTabs")
    assert tabs is not None
    options = panel.findChild(QScrollArea, "mediaToolsOptions")
    assert options is not None
    tabs.setCurrentWidget(options)
    button = panel.findChild(QToolButton, "mediaToolsSectionInfo_overlay_persistence")
    assert button is not None
    assert button.isVisible()
    assert MediaToolsSection.OVERLAY_PERSISTENCE.title in button.toolTip()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    popup = QApplication.activePopupWidget()
    assert popup is not None
    QTest.mouseMove(popup, popup.rect().center())
    assert popup.isVisible()
    if dismiss_key is None:
        QTest.mouseClick(popup, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(popup, dismiss_key)
    assert QApplication.activePopupWidget() is None
