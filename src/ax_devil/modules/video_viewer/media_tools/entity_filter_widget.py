"""Filter UI widget that renders decoder-provided filter options."""

from __future__ import annotations

from typing import Dict, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.filtering import (
    FilterConfig,
    FilterOption,
    FilterState,
)
from ax_devil.modules.filtering.predicate_utils import ClassificationFilter
from ax_devil.modules.scene.filtering import process_scene
from ax_devil.modules.scene.model import Scene


class EntityFilterWidget(QWidget):
    """Filter widget that renders checkboxes from a decoder-supplied config."""

    filterChanged = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        filter_config: FilterConfig | None = None,
        show_title: bool = True,
    ) -> None:
        super().__init__(parent)

        if filter_config is None:
            filter_config = FilterConfig(
                options=(
                    ClassificationFilter(frozenset(), include_empty=True, exclude=True).build_option(
                        id="all",
                        label="All Entities",
                        default_enabled=True,
                        sort_key=0,
                    ),
                ),
                name="Simple Default Filter",
            )
        self._filter_config: FilterConfig = filter_config
        self._filter_state = FilterState(self._filter_config)
        self._show_title = show_title
        self._checkboxes: Dict[str, QCheckBox] = {}
        self._options: Tuple[FilterOption, ...] = ()

        self._setup_ui()
        self._apply_filter_config(self._filter_config)
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filter_group = QGroupBox("Entity Filters") if self._show_title else QWidget()
        filter_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setSpacing(8)
        if not self._show_title:
            filter_layout.setContentsMargins(0, 0, 0, 0)

        self._checkboxes = {}
        self._options = self._filter_config.sorted_options()

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(24)
        controls_layout.addWidget(QLabel("Columns"))

        self._columns_selector = QComboBox()
        self._columns_selector.setEditable(False)
        self._columns_selector.addItem("1", 1)
        self._columns_selector.addItem("2", 2)
        self._columns_selector.addItem("3", 3)
        self._columns_selector.setCurrentIndex(1)
        # Ensure text remains visible even with narrow layouts.
        self._columns_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._columns_selector.setMinimumContentsLength(1)
        self._columns_selector.setMinimumWidth(72)
        controls_layout.addWidget(self._columns_selector)

        self._toggle_all_button = QPushButton("Toggle")
        self._toggle_all_button.setObjectName("toggleAllButton")
        self._toggle_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_all_button.setStyleSheet(
            """
            QPushButton#toggleAllButton {
                border: 1px solid palette(Midlight);
                border-radius: 6px;
                padding: 4px 12px;
                background: palette(Base);
            }
            QPushButton#toggleAllButton:hover {
                border-color: palette(Highlight);
            }
            """
        )
        controls_layout.addWidget(self._toggle_all_button)

        controls_layout.addStretch()
        filter_layout.addLayout(controls_layout)

        self._grid_layout = QGridLayout()
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setHorizontalSpacing(12)
        self._grid_layout.setVerticalSpacing(4)

        filter_layout.addLayout(self._grid_layout)
        filter_layout.addStretch()

        layout.addWidget(filter_group)

    def _connect_signals(self) -> None:
        """Connect UI signals to handlers."""
        self._columns_selector.currentIndexChanged.connect(self._on_columns_changed)
        self._toggle_all_button.clicked.connect(self._on_toggle_all_clicked)

    def _on_filter_changed(self, option_id: str, checked: bool) -> None:
        """Handle filter changes."""
        self._filter_state.set_enabled(option_id, checked)
        self.filterChanged.emit()

    def _on_columns_changed(self, index: int) -> None:
        columns = self._columns_selector.itemData(index)
        if not isinstance(columns, int):
            columns = 2
        self._populate_grid(columns)

    def _on_toggle_all_clicked(self) -> None:
        """Toggle all filters when the outline button is pressed."""
        all_enabled = all(self._filter_state.is_enabled(option.id) for option in self._options)
        self._set_all_filters(not all_enabled)

    def _set_all_filters(self, enabled: bool) -> None:
        """Set all filter options to the provided enabled state."""
        for option in self._options:
            self._filter_state.set_enabled(option.id, enabled)
            checkbox = self._checkboxes.get(option.id)
            if checkbox is None:
                continue
            previous_block = checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(previous_block)
        self.filterChanged.emit()

    def process_scene(self, scene: Scene) -> Scene:
        """Filter scene and return filtered scene."""
        return process_scene(scene, self._filter_config, self._filter_state)

    def set_id_query(self, query: str) -> None:
        """Keep only entities whose id contains ``query``; an empty query keeps all ids."""
        query = query.strip()
        if query == self._filter_state.id_query:
            return
        self._filter_state.id_query = query
        self.filterChanged.emit()

    @property
    def filter_config(self) -> FilterConfig:
        """Return the immutable filter configuration for this widget."""
        return self._filter_config

    @property
    def filter_state(self) -> FilterState:
        """Return the mutable filter state for this widget."""
        return self._filter_state

    def _populate_grid(self, columns: int) -> None:
        max_columns = max(1, min(3, len(self._options)))
        columns = max(1, min(max_columns, columns))

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is not None and (widget := item.widget()):
                self._grid_layout.removeWidget(widget)

        for column in range(max_columns):
            self._grid_layout.setColumnStretch(column, 0)

        for index, option in enumerate(self._options):
            row = index // columns
            column = index % columns
            self._grid_layout.addWidget(self._checkboxes[option.id], row, column)

        for column in range(columns):
            self._grid_layout.setColumnStretch(column, 1)

    def _apply_filter_config(self, filter_config: FilterConfig) -> None:
        """Internal helper to build UI from the supplied filter config."""
        for checkbox in self._checkboxes.values():
            try:
                checkbox.toggled.disconnect()
            except Exception:
                pass
            self._grid_layout.removeWidget(checkbox)
            checkbox.deleteLater()

        self._checkboxes = {}
        self._filter_config = filter_config
        self._filter_state = FilterState(self._filter_config)
        self._options = self._filter_config.sorted_options()

        for option in self._options:
            checkbox = QCheckBox(option.label)
            checkbox.setChecked(self._filter_state.is_enabled(option.id))
            checkbox.toggled.connect(lambda checked, option_id=option.id: self._on_filter_changed(option_id, checked))
            self._checkboxes[option.id] = checkbox

        selected_columns = self._columns_selector.currentData()
        self._populate_grid(selected_columns)
        self._toggle_all_button.setEnabled(bool(self._options))
