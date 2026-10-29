from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.video_viewer import overlay_persistence_views as views_module
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel, OverlayPersistenceControls
from ax_devil.modules.video_viewer.media_tools.appearance_popover import AppearancePopover
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.overlay_persistence_views import OverlayPersistenceViews
from tests.catalog_helpers import create_catalog

_W = TypeVar("_W", bound=QWidget)


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


def _child(controls: OverlayPersistenceControls, kind: type[_W], name: str) -> _W:
    child = controls.findChild(kind, name)
    assert child is not None
    return child


def test_apply_to_all_and_make_default_reach_every_open_control(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ConfigManager().set_config_path(tmp_path / "c.json")
    monkeypatch.setattr(views_module, "_views", OverlayPersistenceViews())
    start = OverlayPersistenceSettings.default_enabled()
    first = OverlayPersistenceControls(initial_settings=start)
    second = OverlayPersistenceControls(initial_settings=start)
    for controls in (first, second):
        qtbot.addWidget(controls)
        controls.show()
    apply_buttons = [_child(controls, QPushButton, "applyStickyToAllButton") for controls in (first, second)]
    default_buttons = [_child(controls, QPushButton, "useStickyAsDefaultButton") for controls in (first, second)]
    labels = [_child(controls, QLabel, "stickyDefaultLabel") for controls in (first, second)]
    opacity_spin = _child(first, QDoubleSpinBox, "overlayOpacitySpin")
    assert not apply_buttons[0].isVisible() and labels[0].isVisible()

    opacity_spin.setValue(0.3)

    assert apply_buttons[0].text() == "Apply to all 2" and apply_buttons[0].isVisible()
    assert default_buttons[0].isVisible() and not labels[0].isVisible()
    with qtbot.waitSignal(second.settingsChanged) as blocker:
        apply_buttons[0].click()
    assert blocker.args[0] == first.current_settings() == second.current_settings()
    assert not apply_buttons[0].isVisible() and not apply_buttons[1].isVisible()

    default_buttons[0].click()

    assert labels[0].isVisible() and labels[1].isVisible()
