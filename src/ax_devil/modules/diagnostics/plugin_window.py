"""Simple window for inspecting plug-in metadata and reloading bundles."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    ApplicationPluginLoader,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.logging_config import get_logger

HandlerDefinition = FileToSceneDecoderDefinition | PayloadToSceneDecoderDefinition


class PluginWindow(ChromeWindow):
    """Minimal plug-in inspector with a reload button."""

    def __init__(self, parent: Optional[QWidget] = None, *, use_custom_frame: bool = False) -> None:
        super().__init__(parent=parent, use_custom_frame=use_custom_frame, show_custom_frame_border=True)
        self.setWindowTitle("Plug-ins")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._logger = get_logger(__name__)
        self._records: list[PluginRecord] = []
        self._table = QTableWidget(self)
        self._status_label = QLabel()
        self._reload_button = QPushButton("Reload plug-ins")
        self._file_table = QTableWidget(self)
        self._decoder_table = QTableWidget(self)

        self._setup_ui()
        self._connect_signals()
        self.refresh()

    def _setup_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        layout.setSpacing(Space.M)

        controls = QHBoxLayout()
        controls.addWidget(self._status_label)
        controls.addStretch()
        controls.addWidget(self._reload_button)
        layout.addLayout(controls)

        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Plugin ID", "Name", "Origin", "Status", "Error"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._table)

        details_layout = QHBoxLayout()
        details_layout.setSpacing(Space.L)

        file_group = QGroupBox("File-to-Scene Decoders")
        file_group_layout = QVBoxLayout(file_group)
        self._configure_handler_table(self._file_table)
        file_group_layout.addWidget(self._file_table)
        details_layout.addWidget(file_group)

        decoder_group = QGroupBox("Decoder Definitions")
        decoder_group_layout = QVBoxLayout(decoder_group)
        self._configure_handler_table(self._decoder_table)
        decoder_group_layout.addWidget(self._decoder_table)
        details_layout.addWidget(decoder_group)

        layout.addLayout(details_layout)

        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        self._reload_button.clicked.connect(self._on_reload_clicked)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

    def _configure_handler_table(self, table: QTableWidget) -> None:
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Handler ID", "Display Name"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

    def refresh(self) -> None:
        """Reload table contents from the registry."""
        records = RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE)
        records.sort(key=lambda record: (record.definition.display_name or record.definition.plugin_id).lower())
        self._records = records
        self._table.setRowCount(len(records))

        for row, record in enumerate(records):
            self._table.setItem(row, 0, QTableWidgetItem(record.definition.plugin_id))
            name_item = QTableWidgetItem(record.definition.display_name)
            if record.definition.description:
                name_item.setToolTip(record.definition.description)
            self._table.setItem(row, 1, name_item)
            origin_item = QTableWidgetItem(record.origin)
            origin_item.setToolTip(str(record.entrypoint))
            self._table.setItem(row, 2, origin_item)

            status_item = QTableWidgetItem(record.status.value)
            status_color = self._status_color(record.status)
            status_item.setData(Qt.ItemDataRole.ForegroundRole, QBrush(status_color))
            self._table.setItem(row, 3, status_item)

            error_text = record.error or "—"
            error_item = QTableWidgetItem(error_text)
            if record.error:
                error_item.setToolTip(record.error)
            self._table.setItem(row, 4, error_item)

        self._table.resizeColumnsToContents()
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._status_label.setText(f"{len(records)} plug-in(s) loaded · Last refresh {timestamp}")
        self._select_first_row()

    def _select_first_row(self) -> None:
        if self._table.rowCount() == 0:
            self._update_handler_tables((), ())
            return
        self._table.selectRow(0)

    def _update_handler_tables(
        self,
        file_handlers: Sequence[FileToSceneDecoderDefinition],
        decoder_definitions: Sequence[PayloadToSceneDecoderDefinition],
    ) -> None:
        self._populate_handler_table(self._file_table, file_handlers)
        self._populate_handler_table(self._decoder_table, decoder_definitions)

    def _populate_handler_table(
        self,
        table: QTableWidget,
        handlers: Sequence[HandlerDefinition],
    ) -> None:
        table.setRowCount(len(handlers))
        for row, handler in enumerate(handlers):
            table.setItem(row, 0, QTableWidgetItem(handler.handler_type))
            display_item = QTableWidgetItem(handler.display_name or handler.handler_type)
            if handler.description:
                display_item.setToolTip(handler.description)
            table.setItem(row, 1, display_item)

    def _on_selection_changed(self) -> None:
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return

        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            self._update_handler_tables((), ())
            return

        row = selected_rows[0].row()
        record = self._get_record_by_row(row)
        if record is None:
            self._update_handler_tables((), ())
            return
        definition = self._decoder_definition(record)
        if definition is None:
            self._update_handler_tables((), ())
            return
        self._update_handler_tables(
            definition.file_to_scene_decoders,
            definition.payload_to_scene_decoders,
        )

    def _get_record_by_row(self, row: int) -> PluginRecord | None:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    @staticmethod
    def _decoder_definition(record: PluginRecord) -> DecoderPluginDefinition | None:
        if record.status != PluginStatus.LOADED:
            return None
        return cast(DecoderPluginDefinition, record.definition)

    def _status_color(self, status: PluginStatus) -> QColor:
        palette = self.palette()
        if status == PluginStatus.LOADED:
            return palette.color(QPalette.ColorRole.Highlight)
        return palette.color(QPalette.ColorRole.Link)

    def _on_reload_clicked(self) -> None:
        self._reload_button.setEnabled(False)
        try:
            ApplicationPluginLoader.reload_plugins()
            self.refresh()
        except Exception as exc:
            self._logger.exception(f"Failed to reload plug-ins: {exc}")
            QMessageBox.critical(self, "Reload failed", str(exc))
        finally:
            self._reload_button.setEnabled(True)
