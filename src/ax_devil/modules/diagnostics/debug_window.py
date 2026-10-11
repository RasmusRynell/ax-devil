"""Viewer-focused rendering diagnostics with bounded history and source details."""

from __future__ import annotations

import json
from datetime import datetime
from time import perf_counter

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.diagnostics.dashboard_snapshot import DashboardSnapshot, DashboardSnapshotService
from ax_devil.modules.diagnostics.history_chart import PaintHistoryChart
from ax_devil.modules.diagnostics.metrics_store import get_metrics_store
from ax_devil.modules.diagnostics.paint_inspection import InspectionSelection, PaintInspection
from ax_devil.modules.diagnostics.paint_inspector import PaintInspector, show_all_rows
from ax_devil.modules.diagnostics.render_metrics import (
    BUILD_STAGES,
    DELIVERY_STAGES,
    HISTORY_LIMIT,
    HISTORY_SECONDS,
    PAINT_STAGES,
    TIMING_STAGES,
    PaintSelection,
    TimingStage,
    ViewerSnapshot,
    get_render_metrics_store,
)
from ax_devil.modules.diagnostics.trace_controls import TraceControls


def _number(value: float | None) -> str:
    if value is None:
        return "—"
    return "<0.001" if 0.0 < value < 0.001 else f"{value:.3f}"


def _label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class DebugWindow(ChromeWindow):
    """Inspect one viewer's frame progress, paint history, and rendering workload."""

    def __init__(self, parent: QWidget | None = None, *, use_custom_frame: bool = False) -> None:
        super().__init__(parent=parent, use_custom_frame=use_custom_frame, show_custom_frame_border=True)
        self.setWindowTitle("Rendering diagnostics")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._snapshot_service = DashboardSnapshotService(get_metrics_store())
        self._capture_enabled = True
        self._snapshot_at = perf_counter()
        self._frozen = False
        self._snapshot: DashboardSnapshot | None = None
        self._inspection_selection: InspectionSelection | None = None
        self._viewers: dict[str, ViewerSnapshot] = {}
        self._viewer_items: dict[str, QListWidgetItem] = {}
        self._source_items: dict[str, QTreeWidgetItem] = {}
        self._setup_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_display)
        self.timer.start()
        self._update_display()

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        controls = QHBoxLayout()
        self.status_label = _label()
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_label.setToolTip(
            f"Recent {HISTORY_SECONDS:g} seconds, limited to {HISTORY_LIMIT} paints per viewer."
        )
        controls.addWidget(self.status_label, 1)
        export = QPushButton("Export")
        export.setToolTip("Export rendering history, source observations, and full cache ranges as JSON.")
        export.clicked.connect(self._export_metrics)
        reset = QPushButton("Reset history")
        reset.setToolTip("Clear rendering measurements for all viewers; playback and source/cache data are unaffected.")
        reset.clicked.connect(self._reset)
        self.freeze_button = QPushButton("Freeze view")
        self.freeze_button.setCheckable(True)
        self.freeze_button.setMinimumWidth(self.freeze_button.fontMetrics().horizontalAdvance("Resume live view") + 32)
        self.freeze_button.setToolTip(
            "Hold this snapshot for inspection. Playback and measurement collection continue."
        )
        self.freeze_button.toggled.connect(self._set_frozen)
        controls.addWidget(self.freeze_button)
        controls.addWidget(export)
        controls.addWidget(reset)
        layout.addLayout(controls)
        self.tabs = QTabWidget()
        tabs = self.tabs
        layout.addWidget(tabs)
        tabs.addTab(self._rendering_tab(), "Rendering")
        tabs.addTab(self._sources_tab(), "Sources && caches")
        tabs.addTab(self._recording_tab(), "Recording")

    def _rendering_tab(self) -> QSplitter:
        rendering = QSplitter()
        rendering.setChildrenCollapsible(False)
        sidebar = QWidget()
        sidebar.setMinimumWidth(140)
        sidebar.setMaximumWidth(300)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Find a viewer")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._filter_viewers)
        sidebar_layout.addWidget(self.filter_input)
        self.viewer_list = QListWidget()
        self.viewer_list.setSpacing(Space.XS)
        self.viewer_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.viewer_list.currentItemChanged.connect(self._show_selected_viewer)
        sidebar_layout.addWidget(self.viewer_list)
        rendering.addWidget(sidebar)

        scroll = QScrollArea()
        self._detail_scroll = scroll
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        detail_layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        detail_layout.setSpacing(Space.M)
        self.title_label = _label()
        TextRole.HEADING.apply(self.title_label)
        detail_layout.addWidget(self.title_label)
        self.empty_label = _label()
        detail_layout.addWidget(self.empty_label)
        self.measurements = QWidget()
        measurements_layout = QVBoxLayout(self.measurements)
        measurements_layout.setContentsMargins(0, 0, 0, 0)
        measurements_layout.setSpacing(Space.XL)

        cadence = QHBoxLayout()
        self.cadence_values: list[QLabel] = []
        for title, scope, note in (
            (
                "New frames / s",
                "observed window",
                "Changed source-frame paints per second in the retained observation window.",
            ),
            (
                "Repaints / s",
                "observed window",
                "Paints of unchanged source frames, including hover and viewport updates.",
            ),
            (
                "Superseded",
                "last 10 seconds",
                "Submissions replaced before painting in the recent 10 seconds (100 ms buckets). Not decoder drops.",
            ),
        ):
            card = QGroupBox(title)
            column = QVBoxLayout(card)
            value = _label("—")
            TextRole.DISPLAY.apply(value)
            value.setWordWrap(False)
            value.setToolTip(note)
            card.setToolTip(note)
            column.addWidget(value)
            column.addWidget(_label(scope))
            cadence.addWidget(card, 1)
            self.cadence_values.append(value)
        measurements_layout.addLayout(cadence)

        pacing = QGroupBox("Timing history")
        pacing_layout = QVBoxLayout(pacing)
        graph_heading = QHBoxLayout()
        self.chart_mode = QComboBox()
        self.chart_mode.addItem("New-frame intervals", "interval")
        self.chart_mode.addItem("CPU rendering work", "paint")
        for stage in TIMING_STAGES:
            if stage.key not in ("interval", "paint"):
                self.chart_mode.addItem(stage.label, stage.key)
        self.chart_mode.currentIndexChanged.connect(self._show_selected_viewer)
        graph_heading.addWidget(self.chart_mode)
        self.inspect_largest = QPushButton("Inspect largest")
        self.inspect_largest.setToolTip(
            "Freeze the visible history and inspect the largest measured value in this graph."
        )
        self.inspect_largest.clicked.connect(self._inspect_largest)
        graph_heading.addWidget(self.inspect_largest)
        self.history_label = _label()
        self.history_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        graph_heading.addWidget(self.history_label, 1)
        pacing_layout.addLayout(graph_heading)
        self.chart = PaintHistoryChart()
        self.chart.sampleSelected.connect(self._inspect_sample)
        pacing_layout.addWidget(self.chart)
        pacing_layout.addWidget(_label("Click a point to freeze and investigate · accent: new frame · muted: repaint"))
        measurements_layout.addWidget(pacing)
        self.detail_tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, Space.L, 0, 0)
        overview_layout.setSpacing(Space.XL)
        self.detail_tabs.addTab(overview, "Overview")
        self.inspector = PaintInspector()
        self.inspector.stepRequested.connect(self._step_inspection)
        self.inspector.recordingRequested.connect(lambda: self.tabs.setCurrentIndex(2))
        self.detail_tabs.addTab(self.inspector, "Inspect paint")
        measurements_layout.addWidget(self.detail_tabs)
        delivery = QGroupBox("Frame delivery")
        delivery_layout = QVBoxLayout(delivery)
        self.delivery_table = self._timing_table(DELIVERY_STAGES)
        delivery_layout.addWidget(self.delivery_table)
        delivery_layout.addWidget(
            _label("Intervals include pauses and seeks. CPU completion is not screen presentation.")
        )
        overview_layout.addWidget(delivery)

        costs = QGroupBox("CPU rendering work")
        costs_layout = QVBoxLayout(costs)
        selection = QHBoxLayout()
        selection.addWidget(_label("Compare"))
        self.paint_selection = QComboBox()
        for choice in PaintSelection:
            self.paint_selection.addItem(choice.value, choice)
        self.paint_selection.setCurrentIndex(1)
        self.paint_selection.currentIndexChanged.connect(self._show_selected_viewer)
        selection.addWidget(self.paint_selection)
        selection.addStretch()
        self.cost_scope = _label()
        self.cost_scope.setWordWrap(False)
        self.cost_scope.setAlignment(Qt.AlignmentFlag.AlignRight)
        selection.addWidget(self.cost_scope)
        costs_layout.addLayout(selection)
        self.timings_table = self._timing_table(PAINT_STAGES)
        costs_layout.addWidget(self.timings_table)
        costs_layout.addWidget(_label("GUI preparation only. GPU and display time are excluded."))
        self.build_table = self._timing_table(BUILD_STAGES)
        build_details = QWidget()
        build_layout = QVBoxLayout(build_details)
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.addWidget(
            _label("Build operations only · drawing build is inside preparation; filtering may precede paint.")
        )
        build_layout.addWidget(self.build_table)
        self._add_disclosure(costs_layout, "Build costs", build_details)
        overview_layout.addWidget(costs)

        workload = QGroupBox("Overlay workload")
        workload_layout = QVBoxLayout(workload)
        workload_rows = QWidget()
        self.workload_labels = self._detail_rows(
            workload_rows,
            ("Backend", "Target", "Entities", "Primitives", "Types", "Cache", "Rebuild", "Reuse / window"),
        )
        workload_layout.addWidget(workload_rows)
        workload_layout.addWidget(_label("Workload describes the last paint. Cache reuse covers all recent paints."))
        overview_layout.addWidget(workload)
        self.progress = QWidget()
        self.progress_labels = self._detail_rows(self.progress, ("Submitted", "Painted", "Overlay", "Superseded total"))
        self._add_disclosure(overview_layout, "Frame details", self.progress)
        detail_layout.addWidget(self.measurements)
        detail_layout.addStretch()
        scroll.setWidget(detail)
        rendering.addWidget(scroll)
        rendering.setSizes([210, 800])
        rendering.setStretchFactor(1, 1)
        return rendering

    @staticmethod
    def _detail_rows(parent: QWidget, names: tuple[str, ...]) -> dict[str, QLabel]:
        layout = QFormLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(Space.XL)
        layout.setVerticalSpacing(Space.S)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        labels = {}
        for name in names:
            value = _label("—")
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addRow(_label(name), value)
            labels[name] = value
        return labels

    @staticmethod
    def _add_disclosure(layout: QVBoxLayout, title: str, content: QWidget) -> None:
        button = QToolButton()
        button.setText(title)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        content.hide()

        def toggle(expanded: bool) -> None:
            content.setVisible(expanded)
            button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

        button.toggled.connect(toggle)
        layout.addWidget(button)
        layout.addWidget(content)

    @staticmethod
    def _timing_table(stages: tuple[TimingStage, ...]) -> QTableWidget:
        table = QTableWidget(len(stages), 6)
        table.setHorizontalHeaderLabels(["Measurement", "Last op ms", "Median ms", "p95 ms", "Max ms", "Samples"])
        table.setToolTip("— = no measurement. Distributions use actual operations in the recent window.")
        table.verticalHeader().hide()
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setMinimumSectionSize(68)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for row, stage in enumerate(stages):
            for column in range(6):
                title = f"    {stage.label}" if stage in PAINT_STAGES[1:] else stage.label
                item = QTableWidgetItem(title if column == 0 else "—")
                item.setToolTip(stage.description)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if stage.key == "paint":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)
        show_all_rows(table)
        return table

    @staticmethod
    def _update_timing_table(
        table: QTableWidget, stages: tuple[TimingStage, ...], viewer: ViewerSnapshot | None, selection: PaintSelection
    ) -> None:
        summaries = viewer.summaries(selection) if viewer else {}
        for row, stage in enumerate(stages):
            summary = summaries.get(stage.key)
            values = (
                summary.last_ms if summary else None,
                summary.median_ms if summary else None,
                summary.p95_ms if summary else None,
                summary.maximum_ms if summary else None,
            )
            for column, value in enumerate(values, start=1):
                cell = table.item(row, column)
                assert cell is not None
                cell.setText(_number(value))
            count = table.item(row, 5)
            assert count is not None
            count.setText(str(summary.count) if summary else "0")

    def _sources_tab(self) -> QWidget:
        sources = QWidget()
        layout = QVBoxLayout(sources)
        note = _label(
            "Snapshot-wide source state · selected-paint context is in Inspect paint · cache capacity is reserved bytes"
        )
        note.setToolTip(
            "Application-wide state. Source values may belong to different frames; "
            "cache reservations exclude decoder and display memory."
        )
        layout.addWidget(note)
        self.source_scope = QCheckBox("Only sources for the selected viewer")
        self.source_scope.setToolTip(
            "Uses explicit source ownership. Shared video caches remain in the application-wide view."
        )
        self.source_scope.toggled.connect(self._refresh_sources)
        layout.addWidget(self.source_scope)
        self.source_tree = QTreeWidget()
        self.source_tree.setHeaderLabels(["Source / observation", "Value", "Observed"])
        self.source_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.source_tree.setUniformRowHeights(True)
        self.source_tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.source_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.source_tree.header().setStretchLastSection(False)
        self.source_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.source_tree.setColumnWidth(0, 320)
        layout.addWidget(self.source_tree)
        return sources

    def _recording_tab(self) -> QWidget:
        recording = QWidget()
        layout = QVBoxLayout(recording)
        layout.addWidget(
            _label(
                "Sample Python stacks to investigate work outside rendering. "
                "Waiting threads are included; native Qt, FFmpeg, and GPU work are not measured."
            )
        )
        self.trace_controls = TraceControls(recording)
        layout.addWidget(self.trace_controls)
        layout.addStretch()
        return recording

    def _set_frozen(self, frozen: bool) -> None:
        self._frozen = frozen
        self.freeze_button.setText("Resume live view" if frozen else "Freeze view")
        if frozen:
            self.status_label.setText("Frozen snapshot · capture continues")
        else:
            self._inspection_selection = None
            self.inspector.show_inspection(None)
            self.detail_tabs.setCurrentIndex(0)
            self._update_display()

    def _update_display(self) -> None:
        if self._frozen:
            return
        snapshot = self._snapshot_service.build_snapshot()
        self._snapshot_at = snapshot.captured_at
        self._snapshot = snapshot
        self._capture_enabled = snapshot.enabled
        self._viewers = {viewer.viewer_id: viewer for viewer in snapshot.viewers}
        count = len(self._viewers)
        self.status_label.setText(
            f"{count} viewer{'s' if count != 1 else ''} · Live measurements"
            if snapshot.enabled
            else "Collect Debug Metrics is off"
        )
        with QSignalBlocker(self.viewer_list):
            for viewer_id in self._viewer_items.keys() - self._viewers.keys():
                removed_item = self._viewer_items.pop(viewer_id)
                self.viewer_list.takeItem(self.viewer_list.row(removed_item))
            for viewer in snapshot.viewers:
                item = self._viewer_items.get(viewer.viewer_id)
                if item is None:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, viewer.viewer_id)
                    self._viewer_items[viewer.viewer_id] = item
                    self.viewer_list.addItem(item)
                item.setText(viewer.label)
                item.setToolTip(f"{viewer.label}\n{viewer.viewer_id}")
            self.viewer_list.sortItems()
            self._filter_viewers()
        self._show_selected_viewer()

    def _filter_viewers(self) -> None:
        needle = self.filter_input.text().casefold()
        first_visible = None
        for index in range(self.viewer_list.count()):
            item = self.viewer_list.item(index)
            item.setHidden(needle not in item.text().casefold())
            if not item.isHidden() and first_visible is None:
                first_visible = item
        current = self.viewer_list.currentItem()
        if current is None or current.isHidden():
            if first_visible is None:
                self.viewer_list.setCurrentRow(-1)
            else:
                self.viewer_list.setCurrentItem(first_visible)

    def _show_selected_viewer(self) -> None:
        current = self.viewer_list.currentItem()
        viewer = self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None
        self._refresh_sources()
        now = self._snapshot_at
        metric_key = self.chart_mode.currentData()
        selection_ref = self._inspection_selection
        if selection_ref and (viewer is None or viewer.viewer_id != selection_ref.viewer_id):
            self._inspection_selection = None
            selection_ref = None
            self.detail_tabs.setCurrentIndex(0)
        selected_index = selection_ref.sample_index if selection_ref else None
        self.chart.set_history(viewer.history if viewer else (), now, metric_key, selected_index)
        self.inspect_largest.setEnabled(
            bool(viewer and any(item.timings[metric_key] is not None for item in viewer.history))
        )
        if viewer is not None and selected_index is not None:
            self._inspection_selection = InspectionSelection(viewer.viewer_id, selected_index, metric_key)
            self.inspector.show_inspection(PaintInspection(viewer, selected_index, metric_key))
        else:
            self.inspector.show_inspection(None)
        self.title_label.setText(viewer.label if viewer else "No matching viewers" if self._viewers else "No viewers")
        self.measurements.setVisible(viewer is not None and viewer.last is not None)
        self.empty_label.setVisible(viewer is None or viewer.last is None)
        self.empty_label.setText(
            "Turn on Debug > Collect Debug Metrics to capture rendering measurements."
            if not self._capture_enabled
            else "Waiting for the first paint."
            if viewer
            else "Try another name or clear the filter."
            if self._viewers
            else "Open a video to inspect rendering."
        )
        last = viewer.last if viewer else None
        selection = PaintSelection(self.paint_selection.currentText())
        self._update_timing_table(self.timings_table, PAINT_STAGES, viewer, selection)
        self._update_timing_table(self.build_table, BUILD_STAGES, viewer, selection)
        self._update_timing_table(self.delivery_table, DELIVERY_STAGES, viewer, PaintSelection.ALL)
        count = len(viewer.observations(selection)) if viewer else 0
        self.cost_scope.setText(f"{count} recent paints")
        if viewer is None:
            return
        selected = viewer.submitted.label if viewer.submitted else "—"
        painted = last.sample.frame.label if last else "—"
        overlay = last.sample.overlay.label if last and last.sample.overlay else "none"
        reused = " · reused" if last and last.sample.overlay_reused else ""
        age = f" · {max(0.0, now - last.sample.completed_at):.1f} s ago" if last else ""
        self.progress_labels["Submitted"].setText(selected)
        self.progress_labels["Painted"].setText(f"{painted}{age}")
        self.progress_labels["Overlay"].setText(f"{overlay}{reused}")
        for label, cadence_text in zip(
            self.cadence_values,
            (f"{viewer.frame_rate:.1f}", f"{viewer.repaint_rate:.1f}", str(viewer.recent_superseded)),
        ):
            label.setText(cadence_text)
        self.progress_labels["Superseded total"].setText(f"{viewer.superseded} since reset")
        self.history_label.setText(f"{viewer.interval_seconds:.1f} s observed · {len(viewer.history)} paints")
        workload_values = dict.fromkeys(self.workload_labels, "—")
        if last:
            sample = last.sample
            generation = sample.generation
            workload_values["Backend"] = sample.backend
            workload_values["Target"] = f"{sample.width} × {sample.height} logical px"
            workload_values["Primitives"] = (
                f"{sample.primitive_count} available · {sample.drawn_primitive_count} submitted for drawing"
            )
            if generation:
                workload_values["Entities"] = (
                    f"{generation.input_entity_count} input → {generation.filtered_entity_count} after filtering"
                )
                workload_values["Types"] = (
                    ", ".join(f"{count} {kind}" for kind, count in generation.primitive_counts) or "none"
                )
                filter_state = "reused" if generation.filter_cache_hit else "built"
                drawing_state = "reused" if generation.drawing_cache_hit else "built"
                workload_values["Cache"] = f"Scene {filter_state} · drawing {drawing_state}"
                workload_values["Rebuild"] = (
                    ", ".join(reason.value for reason in generation.build_reasons) or "No drawing rebuild"
                )
            hits, total = viewer.drawing_cache_reuse
            workload_values["Reuse / window"] = f"{hits} / {total} drawing retrievals" if total else "—"
        for key, detail in workload_values.items():
            self.workload_labels[key].setText(detail)

    def _inspect_sample(self, index: int) -> None:
        current = self.viewer_list.currentItem()
        viewer = self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None
        if viewer is None or not 0 <= index < len(viewer.history):
            return
        # Freeze the exact graph the user clicked; do not fetch a newer snapshot here.
        self.freeze_button.setChecked(True)
        self._inspection_selection = InspectionSelection(viewer.viewer_id, index, self.chart_mode.currentData())
        self._show_selected_viewer()
        self.detail_tabs.setCurrentIndex(1)
        QTimer.singleShot(0, self, self._reveal_inspection)

    def _reveal_inspection(self) -> None:
        if self._inspection_selection is not None:
            self._detail_scroll.ensureWidgetVisible(self.inspector.findings, 12, 12)

    def _inspect_largest(self) -> None:
        current = self.viewer_list.currentItem()
        viewer = self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None
        if viewer is None:
            return
        key = self.chart_mode.currentData()
        candidates = [
            (value, index) for index, item in enumerate(viewer.history) if (value := item.timings[key]) is not None
        ]
        if candidates:
            self._inspect_sample(max(candidates)[1])

    def _step_inspection(self, step: int) -> None:
        if self._inspection_selection is not None:
            self._inspect_sample(self._inspection_selection.sample_index + step)

    def _refresh_sources(self) -> None:
        if self._snapshot is None:
            return
        snapshot = self._snapshot
        current = self.viewer_list.currentItem()
        viewer = self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None
        selected_sources = set(viewer.source_ids) if viewer else set()
        groups: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {}
        for source_id, source in snapshot.sources.items():
            if self.source_scope.isChecked() and source_id not in selected_sources:
                continue
            rows = {
                key: (
                    "—" if item.value is None else str(item.value),
                    f"{max(0.0, self._snapshot_at - item.observed_at):.1f} s ago",
                )
                for key, item in source.observations.items()
            }
            owners = [item.label for item in snapshot.viewers if source_id in item.source_ids]
            rows["Used by"] = (", ".join(owners) or "Application-wide", "")
            groups[source_id] = (source.label, rows)
        if not self.source_scope.isChecked():
            for cache_id, cache in snapshot.caches.items():
                ranges = cache["ranges"]
                summary = ", ".join(f"{start}–{end}" for start, end in ranges[:8]) or "empty"
                if len(ranges) > 8:
                    summary = f"{summary} · {len(ranges) - 8} more ranges in export"
                groups[f"cache:{cache_id}"] = (
                    f"Video cache · {cache['label']}",
                    {
                        "Frames": (str(cache["size"]), "Snapshot"),
                        "Reserved / budget (MiB)": (
                            f"{cache['reserved_bytes'] / 1024**2:.1f} / {cache['budget_bytes'] / 1024**2:g}",
                            "Snapshot",
                        ),
                        "Occupancy": (
                            f"{100 * cache['reserved_bytes'] / cache['budget_bytes']:.1f}%"
                            if cache["budget_bytes"]
                            else "—",
                            "Snapshot",
                        ),
                        "Frame ranges": (summary, "Snapshot"),
                    },
                )
        for name in list(self._source_items):
            if name not in groups:
                item = self._source_items.pop(name)
                self.source_tree.takeTopLevelItem(self.source_tree.indexOfTopLevelItem(item))
        for name, (label, values) in sorted(groups.items(), key=lambda item: item[1][0].casefold()):
            root = self._source_items.get(name)
            if root is None:
                root = QTreeWidgetItem(self.source_tree)
                root.setToolTip(0, name)
                self._source_items[name] = root
            root.setText(0, label)
            observation_count = len(values) - int("Used by" in values)
            root.setText(1, f"{observation_count} observations" if observation_count else "Waiting for observations")
            rows_list = sorted(values.items())
            while root.childCount() > len(rows_list):
                root.takeChild(root.childCount() - 1)
            for index, (key, (value, age)) in enumerate(rows_list):
                existing = root.child(index)
                item = existing if existing is not None else QTreeWidgetItem(root)
                item.setText(0, key)
                item.setText(1, value)
                item.setToolTip(1, value)
                item.setText(2, age)

    def _reset(self) -> None:
        get_render_metrics_store().clear()
        self.freeze_button.setChecked(False)
        self._update_display()

    def _export_metrics(self) -> None:
        filename = f"rendering-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export diagnostics", filename, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as output:
                json.dump(
                    self._snapshot_service.build_export_payload(
                        snapshot=self._snapshot if self._frozen else None, inspection=self._inspection_selection
                    ),
                    output,
                    indent=2,
                )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Export diagnostics", str(error))

    def cleanup(self) -> None:
        """Stop refresh and release a running trace recorder."""
        self.timer.stop()
        self.inspector.cleanup()
        self.trace_controls.cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Release diagnostics-owned resources before closing."""
        self.cleanup()
        super().closeEvent(event)
