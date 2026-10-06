"""Render catalog selection controls for media tools."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.scene.rendering import SceneRenderCatalog, SceneRenderCatalogSelection


class SceneRenderCatalogSelector(QGroupBox):
    """Display and command one Scene Render Catalog selection.

    The widget is redrawn entirely from the selection and the shared listing whenever the selection changes, which
    includes every listing change.
    """

    catalogViewerRequested = Signal()

    def __init__(
        self,
        render_catalog_selection: SceneRenderCatalogSelection,
        parent: QWidget | None = None,
        *,
        show_title: bool = True,
    ) -> None:
        super().__init__("Select Render Catalog" if show_title else "", parent)
        self._render_catalog_selection = render_catalog_selection
        self._render_catalog_manager = render_catalog_selection.manager
        self._show_title = show_title

        self._catalog_combo = QComboBox(self)
        self._catalog_combo.setObjectName("renderCatalogCombo")

        self._open_catalogs_button = QToolButton(self)
        self._open_catalogs_button.setObjectName("openRenderCatalogsButton")
        self._open_catalogs_button.setText("View")
        self._open_catalogs_button.setToolTip("Show this catalog on example objects in the catalog viewer")
        self._open_catalogs_button.clicked.connect(self.catalogViewerRequested.emit)

        self._reload_button = QPushButton("Reload", self)
        self._reload_button.setObjectName("reloadRenderCatalogButton")
        self._reload_button.clicked.connect(self._render_catalog_selection.reload_active_catalog)

        self._catalog_actions_menu = QMenu(self)
        self._apply_to_all_action = self._catalog_actions_menu.addAction("Apply to all")
        self._apply_to_all_action.setObjectName("applyRenderCatalogToAllAction")
        self._apply_to_all_action.setToolTip("Use this catalog in every open viewer and lane.")
        self._apply_to_all_action.triggered.connect(self._render_catalog_selection.apply_to_all)
        self._use_as_default_action = self._catalog_actions_menu.addAction("Use as default")
        self._use_as_default_action.setObjectName("useRenderCatalogAsDefaultAction")
        self._use_as_default_action.setToolTip("Start new viewers and lanes with this catalog.")
        self._use_as_default_action.triggered.connect(self._render_catalog_selection.use_as_default)
        self._catalog_actions_menu.setToolTipsVisible(True)

        self._catalog_actions_button = QToolButton(self)
        self._catalog_actions_button.setObjectName("renderCatalogActionsButton")
        self._catalog_actions_button.setText("⋯")
        self._catalog_actions_button.setToolTip("More catalog actions")
        self._catalog_actions_button.setMenu(self._catalog_actions_menu)
        self._catalog_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._catalog_actions_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")

        self._status_label = QLabel(self)
        self._status_label.setObjectName("renderCatalogStatus")
        self._status_label.setWordWrap(True)

        self._setup_layout()
        self._render_catalog_selection.selectionChanged.connect(self._render)
        self._render()
        self._catalog_combo.currentIndexChanged.connect(self._select_current_catalog)

    @property
    def active_catalog(self) -> SceneRenderCatalog | None:
        """Return the selection's active compiled catalog."""
        return self._render_catalog_selection.active_catalog()

    def refresh_catalogs(self) -> None:
        """Ask the shared manager to refresh catalog metadata."""
        self._render_catalog_selection.refresh_catalogs()

    def reload_selected_catalog(self) -> None:
        """Ask the selection to reload the active catalog."""
        self._render_catalog_selection.reload_active_catalog()

    def _setup_layout(self) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._catalog_combo, 1)
        row.addWidget(self._open_catalogs_button)
        row.addWidget(self._reload_button)
        row.addWidget(self._catalog_actions_button)

        layout = QVBoxLayout(self)
        if not self._show_title:
            layout.setContentsMargins(0, 0, 0, 0)
            self.setFlat(True)
            self.setStyleSheet("QGroupBox { border: none; margin-top: 0; padding: 0; }")
        layout.addLayout(row)
        layout.addWidget(self._status_label)

    def _render(self) -> None:
        listing = self._render_catalog_manager.listing()
        active_path = self._render_catalog_selection.active_catalog_path()
        paths = [catalog.path for catalog in listing.catalogs]
        with QSignalBlocker(self._catalog_combo):
            self._catalog_combo.clear()
            for path in paths if active_path in paths else [*paths, active_path]:
                self._catalog_combo.addItem(listing.label(path), str(path))
            self._catalog_combo.setCurrentIndex(self._catalog_combo.findData(str(active_path)))
        # Only a catalog this view shows can be passed on; a failed load stays failed here until reloaded.
        shown = self._render_catalog_selection.active_catalog_loaded()
        self._apply_to_all_action.setEnabled(shown and self._render_catalog_manager.can_apply_to_all(active_path))
        self._use_as_default_action.setEnabled(shown and self._render_catalog_manager.can_use_as_default(active_path))
        self._status_label.setText(self._render_catalog_selection.status())

    def _select_current_catalog(self, *_args: object) -> None:
        catalog_path = self._selected_catalog_path()
        if catalog_path is None:
            return
        if catalog_path == self._render_catalog_selection.active_catalog_path():
            return
        self._render_catalog_selection.select_catalog(catalog_path)

    def _selected_catalog_path(self) -> Path | None:
        value = self._catalog_combo.currentData()
        if not isinstance(value, str) or not value:
            return None
        return Path(value)
