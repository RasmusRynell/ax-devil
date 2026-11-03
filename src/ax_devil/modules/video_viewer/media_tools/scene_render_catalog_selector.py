"""Render catalog selection controls for media tools."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.scene.rendering import SceneRenderCatalog, SceneRenderCatalogSelection

_MUTED = "color: palette(placeholder-text);"
_COMBO_CHARS = 8


class SceneRenderCatalogSelector(QWidget):
    """Display and command one Scene Render Catalog selection.

    The widget is redrawn entirely from the selection and the shared listing whenever the selection changes, which
    includes every listing change and every change in which catalogs other open views use. It says whether this
    catalog is the default for new views, and names the catalogs other open views show when they differ.
    """

    catalogViewerRequested = Signal()

    def __init__(self, render_catalog_selection: SceneRenderCatalogSelection, parent: QWidget | None = None) -> None:
        """Create the selector for *render_catalog_selection*."""
        super().__init__(parent)
        self._render_catalog_selection = render_catalog_selection
        self._render_catalog_manager = render_catalog_selection.manager

        self._catalog_combo = QComboBox(self)
        self._catalog_combo.setObjectName("renderCatalogCombo")
        self._catalog_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._catalog_combo.setMinimumContentsLength(_COMBO_CHARS)

        self._open_catalogs_button = SmallButton("View", parent=self)
        self._open_catalogs_button.setObjectName("openRenderCatalogsButton")
        self._open_catalogs_button.setToolTip("Show this catalog on example objects in the catalog viewer")
        self._open_catalogs_button.clicked.connect(self.catalogViewerRequested.emit)

        self._reload_button = SmallButton("Reload", parent=self)
        self._reload_button.setObjectName("reloadRenderCatalogButton")
        self._reload_button.clicked.connect(self._render_catalog_selection.reload_active_catalog)

        # One line that never widens the popover: a long name or error is cut in the middle, whole in the tooltip.
        self._status_label = ElidedLabel("", Qt.TextElideMode.ElideMiddle, self)
        self._status_label.setObjectName("renderCatalogStatus")

        self._default_label = QLabel("Default for new views", self)
        self._default_label.setObjectName("renderCatalogDefaultLabel")
        self._make_default_button = SmallButton("Make default", parent=self)
        self._make_default_button.setObjectName("useRenderCatalogAsDefaultButton")
        self._make_default_button.setToolTip("Start new viewers and lanes with this catalog.")
        self._make_default_button.clicked.connect(self._render_catalog_selection.use_as_default)

        self._others_label = ElidedLabel("", Qt.TextElideMode.ElideRight, self)
        self._others_label.setObjectName("renderCatalogOthersLabel")
        self._apply_to_all_button = SmallButton("Apply to all", parent=self)
        self._apply_to_all_button.setObjectName("applyRenderCatalogToAllButton")
        self._apply_to_all_button.setToolTip("Use this catalog in every open viewer and lane.")
        self._apply_to_all_button.clicked.connect(self._render_catalog_selection.apply_to_all)

        for muted in (self._status_label, self._default_label, self._others_label):
            muted.setStyleSheet(_MUTED)
            TextRole.SMALL.apply(muted)

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
        catalog_row = QHBoxLayout()
        catalog_row.setContentsMargins(0, 0, 0, 0)
        catalog_row.setSpacing(Space.S)
        catalog_row.addWidget(self._catalog_combo, 1)
        catalog_row.addWidget(self._open_catalogs_button)
        catalog_row.addWidget(self._reload_button)

        default_row = QHBoxLayout()
        default_row.setContentsMargins(0, 0, 0, 0)
        default_row.addWidget(self._default_label)
        default_row.addWidget(self._make_default_button)
        default_row.addStretch(1)

        others_row = QHBoxLayout()
        others_row.setContentsMargins(0, 0, 0, 0)
        others_row.setSpacing(Space.S)
        others_row.addWidget(self._others_label, 1)
        others_row.addWidget(self._apply_to_all_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        layout.addLayout(catalog_row)
        layout.addWidget(self._status_label)
        layout.addLayout(default_row)
        layout.addLayout(others_row)

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
        is_default = active_path == listing.chosen_default_path
        self._default_label.setVisible(is_default)
        self._make_default_button.setVisible(not is_default)
        self._make_default_button.setEnabled(shown and self._render_catalog_manager.can_use_as_default(active_path))
        open_views = self._render_catalog_manager.catalogs_in_use.paths()
        others = list(dict.fromkeys(path for path in open_views if path != active_path))
        self._others_label.set_full_text(f"Other views use: {', '.join(listing.label(path) for path in others)}")
        self._others_label.setVisible(bool(others))
        self._apply_to_all_button.setText(f"Apply to all {len(open_views)}")
        self._apply_to_all_button.setVisible(bool(others))
        self._apply_to_all_button.setEnabled(shown and self._render_catalog_manager.can_apply_to_all(active_path))
        self._status_label.set_full_text(self._render_catalog_selection.status())

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
