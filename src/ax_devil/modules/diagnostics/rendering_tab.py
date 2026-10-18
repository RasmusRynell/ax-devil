"""The Rendering tab: pick a viewer, then read its cadence, timing history, paint inspection and overlay workload."""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.diagnostics.dashboard_snapshot import DashboardSnapshot
from ax_devil.modules.diagnostics.detail_widgets import add_disclosure, detail_rows, selectable_label
from ax_devil.modules.diagnostics.history_chart import PaintHistoryChart
from ax_devil.modules.diagnostics.overlay_workload import WORKLOAD_ROWS, workload_texts
from ax_devil.modules.diagnostics.paint_inspection import InspectionSelection, PaintInspection
from ax_devil.modules.diagnostics.paint_inspector import PaintInspector
from ax_devil.modules.diagnostics.render_metrics import (
    BUILD_STAGES,
    DELIVERY_STAGES,
    PAINT_STAGES,
    TIMING_STAGES,
    PaintSelection,
    ViewerSnapshot,
)
from ax_devil.modules.diagnostics.timing_tables import timing_table, update_timing_table


class RenderingTab(QSplitter):
    """A list of viewers beside the selected viewer's measurements.

    Inspecting a paint asks the window to freeze its view (``freezeRequested``); ``viewerChanged`` tells it the
    selection changed, and ``recordingRequested`` that the user wants the Recording tab.
    """

    viewerChanged = Signal()
    freezeRequested = Signal()
    recordingRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._capture_enabled = True
        self._snapshot_at = perf_counter()
        self._viewers: dict[str, ViewerSnapshot] = {}
        self._viewer_items: dict[str, QListWidgetItem] = {}
        self.inspection_selection: InspectionSelection | None = None
        self.setChildrenCollapsible(False)
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
        self.addWidget(sidebar)

        scroll = QScrollArea()
        self._detail_scroll = scroll
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        detail_layout.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        detail_layout.setSpacing(Space.M)
        self.title_label = selectable_label()
        TextRole.HEADING.apply(self.title_label)
        detail_layout.addWidget(self.title_label)
        self.empty_label = selectable_label()
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
            value = selectable_label("—")
            TextRole.DISPLAY.apply(value)
            value.setWordWrap(False)
            value.setToolTip(note)
            card.setToolTip(note)
            column.addWidget(value)
            column.addWidget(selectable_label(scope))
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
        self.history_label = selectable_label()
        self.history_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        graph_heading.addWidget(self.history_label, 1)
        pacing_layout.addLayout(graph_heading)
        self.chart = PaintHistoryChart()
        self.chart.sampleSelected.connect(self._inspect_sample)
        pacing_layout.addWidget(self.chart)
        pacing_layout.addWidget(
            selectable_label("Click a point to freeze and investigate · accent: new frame · muted: repaint")
        )
        measurements_layout.addWidget(pacing)
        self.detail_tabs = QTabWidget()
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, Space.L, 0, 0)
        overview_layout.setSpacing(Space.XL)
        self.detail_tabs.addTab(overview, "Overview")
        self.inspector = PaintInspector()
        self.inspector.stepRequested.connect(self._step_inspection)
        self.inspector.recordingRequested.connect(self.recordingRequested)
        self.detail_tabs.addTab(self.inspector, "Inspect paint")
        measurements_layout.addWidget(self.detail_tabs)
        delivery = QGroupBox("Frame delivery")
        delivery_layout = QVBoxLayout(delivery)
        self.delivery_table = timing_table(DELIVERY_STAGES)
        delivery_layout.addWidget(self.delivery_table)
        delivery_layout.addWidget(
            selectable_label("Intervals include pauses and seeks. CPU completion is not screen presentation.")
        )
        overview_layout.addWidget(delivery)

        costs = QGroupBox("CPU rendering work")
        costs_layout = QVBoxLayout(costs)
        selection = QHBoxLayout()
        selection.addWidget(selectable_label("Compare"))
        self.paint_selection = QComboBox()
        for choice in PaintSelection:
            self.paint_selection.addItem(choice.value, choice)
        self.paint_selection.setCurrentIndex(1)
        self.paint_selection.currentIndexChanged.connect(self._show_selected_viewer)
        selection.addWidget(self.paint_selection)
        selection.addStretch()
        self.cost_scope = selectable_label()
        self.cost_scope.setWordWrap(False)
        self.cost_scope.setAlignment(Qt.AlignmentFlag.AlignRight)
        selection.addWidget(self.cost_scope)
        costs_layout.addLayout(selection)
        self.timings_table = timing_table(PAINT_STAGES)
        costs_layout.addWidget(self.timings_table)
        costs_layout.addWidget(selectable_label("GUI preparation only. GPU and display time are excluded."))
        self.build_table = timing_table(BUILD_STAGES)
        build_details = QWidget()
        build_layout = QVBoxLayout(build_details)
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.addWidget(
            selectable_label(
                "Build operations only · drawing build is inside preparation; filtering may precede paint."
            )
        )
        build_layout.addWidget(self.build_table)
        add_disclosure(costs_layout, "Build costs", build_details)
        overview_layout.addWidget(costs)

        workload = QGroupBox("Overlay workload")
        workload_layout = QVBoxLayout(workload)
        workload_rows = QWidget()
        self.workload_labels = detail_rows(
            workload_rows,
            WORKLOAD_ROWS,
        )
        workload_layout.addWidget(workload_rows)
        workload_layout.addWidget(
            selectable_label("Workload describes the last paint. Cache reuse covers all recent paints.")
        )
        overview_layout.addWidget(workload)
        self.progress = QWidget()
        self.progress_labels = detail_rows(self.progress, ("Submitted", "Painted", "Overlay", "Superseded total"))
        add_disclosure(overview_layout, "Frame details", self.progress)
        detail_layout.addWidget(self.measurements)
        detail_layout.addStretch()
        scroll.setWidget(detail)
        self.addWidget(scroll)
        self.setSizes([210, 800])
        self.setStretchFactor(1, 1)

    def show_snapshot(self, snapshot: DashboardSnapshot) -> None:
        """Show *snapshot*: the viewers it lists and the measurements of the selected one."""
        self._snapshot_at = snapshot.captured_at
        self._capture_enabled = snapshot.enabled
        self._viewers = {viewer.viewer_id: viewer for viewer in snapshot.viewers}
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

    def selected_viewer(self) -> ViewerSnapshot | None:
        """Return the viewer selected in the list, if any."""
        current = self.viewer_list.currentItem()
        return self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None

    def clear_inspection(self) -> None:
        """Leave paint inspection and show the overview again."""
        self.inspection_selection = None
        self.inspector.show_inspection(None)
        self.detail_tabs.setCurrentIndex(0)

    def cleanup(self) -> None:
        """Release the paint inspector's resources."""
        self.inspector.cleanup()

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
        self.viewerChanged.emit()
        now = self._snapshot_at
        metric_key = self.chart_mode.currentData()
        selection_ref = self.inspection_selection
        if selection_ref and (viewer is None or viewer.viewer_id != selection_ref.viewer_id):
            self.inspection_selection = None
            selection_ref = None
            self.detail_tabs.setCurrentIndex(0)
        selected_index = selection_ref.sample_index if selection_ref else None
        self.chart.set_history(viewer.history if viewer else (), now, metric_key, selected_index)
        self.inspect_largest.setEnabled(
            bool(viewer and any(item.timings[metric_key] is not None for item in viewer.history))
        )
        if viewer is not None and selected_index is not None:
            self.inspection_selection = InspectionSelection(viewer.viewer_id, selected_index, metric_key)
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
        update_timing_table(self.timings_table, PAINT_STAGES, viewer, selection)
        update_timing_table(self.build_table, BUILD_STAGES, viewer, selection)
        update_timing_table(self.delivery_table, DELIVERY_STAGES, viewer, PaintSelection.ALL)
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
        for key, detail in workload_texts(viewer).items():
            self.workload_labels[key].setText(detail)

    def _inspect_sample(self, index: int) -> None:
        current = self.viewer_list.currentItem()
        viewer = self._viewers.get(current.data(Qt.ItemDataRole.UserRole)) if current else None
        if viewer is None or not 0 <= index < len(viewer.history):
            return
        # Freeze the exact graph the user clicked; do not fetch a newer snapshot here.
        self.freezeRequested.emit()
        self.inspection_selection = InspectionSelection(viewer.viewer_id, index, self.chart_mode.currentData())
        self._show_selected_viewer()
        self.detail_tabs.setCurrentIndex(1)
        QTimer.singleShot(0, self, self._reveal_inspection)

    def _reveal_inspection(self) -> None:
        if self.inspection_selection is not None:
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
        if self.inspection_selection is not None:
            self._inspect_sample(self.inspection_selection.sample_index + step)
