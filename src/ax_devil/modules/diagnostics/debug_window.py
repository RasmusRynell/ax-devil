"""Viewer-focused rendering diagnostics with bounded history and source details."""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.diagnostics.dashboard_snapshot import DashboardSnapshot, DashboardSnapshotService
from ax_devil.modules.diagnostics.detail_widgets import selectable_label
from ax_devil.modules.diagnostics.metrics_store import get_metrics_store
from ax_devil.modules.diagnostics.render_metrics import (
    HISTORY_LIMIT,
    HISTORY_SECONDS,
    get_render_metrics_store,
)
from ax_devil.modules.diagnostics.rendering_tab import RenderingTab
from ax_devil.modules.diagnostics.sources_tab import SourcesTab
from ax_devil.modules.diagnostics.trace_controls import TraceControls


class DebugWindow(ChromeWindow):
    """Inspect one viewer's frame progress, paint history, and rendering workload."""

    def __init__(self, parent: QWidget | None = None, *, use_custom_frame: bool = False) -> None:
        super().__init__(parent=parent, use_custom_frame=use_custom_frame, show_custom_frame_border=True)
        self.setWindowTitle("Rendering diagnostics")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._snapshot_service = DashboardSnapshotService(get_metrics_store())
        self._frozen = False
        self._snapshot: DashboardSnapshot | None = None
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
        self.status_label = selectable_label()
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
        follow_appearance(
            self.freeze_button,
            lambda: self.freeze_button.setMinimumWidth(
                self.freeze_button.fontMetrics().horizontalAdvance("Resume live view") + 32
            ),
        )
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
        self.rendering = RenderingTab()
        self.rendering.viewerChanged.connect(self._refresh_sources)
        self.rendering.freezeRequested.connect(lambda: self.freeze_button.setChecked(True))
        self.rendering.recordingRequested.connect(lambda: tabs.setCurrentIndex(2))
        tabs.addTab(self.rendering, "Rendering")
        self.sources_tab = SourcesTab()
        self.sources_tab.scope.toggled.connect(self._refresh_sources)
        tabs.addTab(self.sources_tab, "Sources && caches")
        tabs.addTab(self._recording_tab(), "Recording")

    def _recording_tab(self) -> QWidget:
        recording = QWidget()
        layout = QVBoxLayout(recording)
        layout.addWidget(
            selectable_label(
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
            self.rendering.clear_inspection()
            self._update_display()

    def _update_display(self) -> None:
        if self._frozen:
            return
        snapshot = self._snapshot_service.build_snapshot()
        self._snapshot = snapshot
        count = len(snapshot.viewers)
        self.status_label.setText(
            f"{count} viewer{'s' if count != 1 else ''} · Live measurements"
            if snapshot.enabled
            else "Collect Debug Metrics is off"
        )
        self.rendering.show_snapshot(snapshot)

    def _refresh_sources(self) -> None:
        if self._snapshot is not None:
            self.sources_tab.show_snapshot(self._snapshot, self._snapshot.captured_at, self.rendering.selected_viewer())

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
                        snapshot=self._snapshot if self._frozen else None,
                        inspection=self.rendering.inspection_selection,
                    ),
                    output,
                    indent=2,
                )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Export diagnostics", str(error))

    def cleanup(self) -> None:
        """Stop refresh and release a running trace recorder."""
        self.timer.stop()
        self.rendering.cleanup()
        self.trace_controls.cleanup()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Release diagnostics-owned resources before closing."""
        self.cleanup()
        super().closeEvent(event)
