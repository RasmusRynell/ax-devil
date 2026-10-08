"""Filter UI widget that renders decoder-provided filter options."""

from __future__ import annotations

from PySide6.QtCore import Qt
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

from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.filtering import FilterOption
from ax_devil.modules.filtering.session_filter import SessionFilter


class EntityFilterWidget(QWidget):
    """Filter widget that renders checkboxes from a decoder-supplied config."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        filter_model: SessionFilter,
        show_title: bool = True,
    ) -> None:
        super().__init__(parent)

        self._filter_model = filter_model
        self._show_title = show_title
        self._checkboxes: dict[str, QCheckBox] = {}
        self._options: tuple[FilterOption, ...] = ()

        self._setup_ui()
        self._build_controls()
        self._filter_model.changed.connect(self._refresh_controls)
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        filter_group = QGroupBox("Entity Filters") if self._show_title else QWidget()
        filter_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setSpacing(Space.M)
        if not self._show_title:
            filter_layout.setContentsMargins(0, 0, 0, 0)

        self._checkboxes = {}
        self._options = self._filter_model.options

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(Space.XL)
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
        controls_layout.addWidget(self._columns_selector)

        self._toggle_all_button = QPushButton("Toggle")
        self._toggle_all_button.setObjectName("toggleAllButton")
        self._toggle_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_all_button.setStyleSheet(
            f"""
            QPushButton#toggleAllButton {{
                border: 1px solid palette(Midlight);
                border-radius: {Radius.CONTROL}px;
                padding: {Space.S}px {Space.L}px;
                background: palette(Base);
            }}
            QPushButton#toggleAllButton:hover {{
                border-color: palette(Highlight);
            }}
            """
        )
        controls_layout.addWidget(self._toggle_all_button)

        controls_layout.addStretch()
        filter_layout.addLayout(controls_layout)

        self._grid_layout = QGridLayout()
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setHorizontalSpacing(Space.L)
        self._grid_layout.setVerticalSpacing(Space.S)

        filter_layout.addLayout(self._grid_layout)
        filter_layout.addStretch()

        layout.addWidget(filter_group)

    def _connect_signals(self) -> None:
        """Connect UI signals to handlers."""
        self._columns_selector.currentIndexChanged.connect(self._on_columns_changed)
        self._toggle_all_button.clicked.connect(self._on_toggle_all_clicked)

    def _on_filter_changed(self, option_id: str, checked: bool) -> None:
        """Handle filter changes."""
        self._filter_model.set_enabled(option_id, checked)

    def _on_columns_changed(self, index: int) -> None:
        columns = self._columns_selector.itemData(index)
        if not isinstance(columns, int):
            columns = 2
        self._populate_grid(columns)

    def _on_toggle_all_clicked(self) -> None:
        """Toggle all filters when the outline button is pressed."""
        self._filter_model.toggle_all()

    def _refresh_controls(self) -> None:
        for option_id, checkbox in self._checkboxes.items():
            previous_block = checkbox.blockSignals(True)
            checkbox.setChecked(self._filter_model.is_enabled(option_id))
            checkbox.blockSignals(previous_block)

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

    def _build_controls(self) -> None:
        """Build controls from the session filter options."""
        self._checkboxes = {}
        self._options = self._filter_model.options

        for option in self._options:
            checkbox = QCheckBox(option.label)
            checkbox.setChecked(self._filter_model.is_enabled(option.id))
            checkbox.toggled.connect(lambda checked, option_id=option.id: self._on_filter_changed(option_id, checked))
            self._checkboxes[option.id] = checkbox

        selected_columns = self._columns_selector.currentData()
        self._populate_grid(selected_columns)
        self._toggle_all_button.setEnabled(bool(self._options))
