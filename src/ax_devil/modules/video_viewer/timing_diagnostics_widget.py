"""Compact timing and overlay-alignment status for offline video lanes."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget

from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentReport, VideoTimingProfile
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy

_RESOURCE_PACKAGE = "ax_devil.resources"
_WARNING_ICON = "warning-icon.png"
_INDICATOR_ICON_SIZE = 16
_INDICATOR_WIDTH = _INDICATOR_ICON_SIZE + 8
_MODE_BUTTON_HEIGHT = 22


def _load_tinted_pixmap(filename: str, color: QColor, size: int) -> QPixmap:
    """Load a packaged icon scaled to ``size`` and recolored with a flat tint."""
    icon_path = files(_RESOURCE_PACKAGE).joinpath(filename)
    image = QImage(str(icon_path))
    if image.isNull():
        return QPixmap()
    image = image.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    tinted = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawImage(0, 0, image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QPixmap.fromImage(tinted)


class OverlayAlignmentIndicator(QLabel):
    """Warning icon shown in a lane header only when overlay timestamps misalign."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the indicator hidden until a problematic alignment report arrives."""
        super().__init__(parent)
        self.setObjectName("overlay-alignment-indicator")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(_INDICATOR_WIDTH)
        self._report: OverlayAlignmentReport | None = None
        self._refresh_icons()
        self.setVisible(False)

    def _refresh_icons(self) -> None:
        self._caution_pixmap = _load_tinted_pixmap(
            _WARNING_ICON, StatusColor.WARNING.color(self.palette()), _INDICATOR_ICON_SIZE
        )
        self._weak_pixmap = _load_tinted_pixmap(
            _WARNING_ICON, StatusColor.ERROR.color(self.palette()), _INDICATOR_ICON_SIZE
        )

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Retint an already displayed warning when the palette changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._refresh_icons()
            self.update_alignment_report(self._report)

    def update_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        """Show a tinted warning icon when overlay timing needs attention."""
        self._report = report
        if report is None or report.total_overlay_frames <= 0:
            self.setVisible(False)
            return
        if not report.has_alignment_warning:
            self.setVisible(False)
            return
        percent = report.policy_match_ratio * 100.0
        self.setPixmap(self._weak_pixmap if report.has_poor_policy_alignment else self._caution_pixmap)
        self.setToolTip(f"Overlay timestamp match {percent:.0f}%.\n{report.alignment_issue_text}")
        self.setVisible(True)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        """Show the alignment tooltip immediately instead of after the default delay."""
        tooltip = self.toolTip()
        if tooltip:
            QToolTip.showText(QCursor.pos(), tooltip, self)
        super().enterEvent(event)


class TimingDiagnosticsWidget(QWidget):
    """Show decoded video cadence and overlay timestamp alignment controls."""

    timestampFallbackPolicyChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the controls widget."""
        super().__init__(parent)
        self._report: OverlayAlignmentReport | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._timing_label = QLabel("Timing: unknown", self)
        self._alignment_label = QLabel("Overlay: none", self)
        self._timing_label.setWordWrap(True)
        self._alignment_label.setWordWrap(True)
        layout.addWidget(self._timing_label)
        layout.addWidget(self._alignment_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self._exact_button = QPushButton("Exact", self)
        self._previous_button = QPushButton("Previous", self)
        self._mode_group = QButtonGroup(self)
        self._timestamp_fallback_policy = TimestampFallbackPolicy()
        self._mode_group.setExclusive(True)
        for button in (self._exact_button, self._previous_button):
            button.setCheckable(True)
            button.setFixedHeight(_MODE_BUTTON_HEIGHT)
            button_row.addWidget(button)
            self._mode_group.addButton(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        self._previous_button.setChecked(True)
        self._exact_button.setToolTip("Disable timestamp fallback after exact and sequence lookup")
        self._previous_button.setToolTip("Use the previous overlay timestamp within the configured tolerance")
        self._exact_button.clicked.connect(lambda _checked=False: self._emit_timestamp_fallback_policy())
        self._previous_button.clicked.connect(lambda _checked=False: self._emit_timestamp_fallback_policy())

        self.setObjectName("timing-diagnostics-widget")
        self.setStyleSheet("#timing-diagnostics-widget QPushButton { padding: 1px 8px; }")

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Refresh the displayed report's status color after a theme switch."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self.update_alignment_report(self._report)

    def _emit_timestamp_fallback_policy(self) -> None:
        """Emit the selected timestamp fallback policy."""
        mode = (
            TimestampFallbackMode.EXACT_ONLY
            if self._exact_button.isChecked()
            else TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE
        )
        self._timestamp_fallback_policy = TimestampFallbackPolicy(
            mode=mode,
            tolerance_us=self._timestamp_fallback_policy.tolerance_us,
        )
        self.timestampFallbackPolicyChanged.emit(self._timestamp_fallback_policy)

    def timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        """Return the selected timestamp fallback policy."""
        return self._timestamp_fallback_policy

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        """Update selected timestamp fallback policy without emitting a change."""
        self._timestamp_fallback_policy = policy
        self._exact_button.blockSignals(True)
        self._previous_button.blockSignals(True)
        self._exact_button.setChecked(policy.mode is TimestampFallbackMode.EXACT_ONLY)
        self._previous_button.setChecked(policy.mode is TimestampFallbackMode.PREVIOUS_WITH_TOLERANCE)
        self._exact_button.blockSignals(False)
        self._previous_button.blockSignals(False)

    def set_timestamp_fallback_controls_enabled(self, enabled: bool) -> None:
        """Enable timestamp fallback controls when an overlay source is present."""
        self._exact_button.setEnabled(enabled)
        self._previous_button.setEnabled(enabled)

    def update_timing_profile(self, profile: VideoTimingProfile | None) -> None:
        """Update video timing status text."""
        if profile is None:
            self._timing_label.setText("Timing: unknown")
            return
        if profile.is_variable_cadence:
            self._timing_label.setText(f"Timing: variable ({profile.primary_periods_text})")
            return
        if profile.period_modes_us:
            self._timing_label.setText(f"Timing: constant ({profile.primary_periods_text})")
            return
        self._timing_label.setText("Timing: unknown")

    def update_alignment_report(self, report: OverlayAlignmentReport | None) -> None:
        """Update overlay alignment status text."""
        self._report = report
        if report is None:
            self._alignment_label.setText("Overlay: none")
            self._alignment_label.setStyleSheet("")
            return
        policy_ratio = report.policy_match_ratio * 100.0
        if report.total_video_frames <= 0 and report.total_overlay_frames > 0:
            prefix = "no video timing"
        elif report.has_suspicious_last_overlay_timestamp:
            prefix = "coverage/clock warning"
        else:
            prefix = "weak match" if report.has_poor_policy_alignment else "match"
        color = (
            StatusColor.WARNING
            if report.has_poor_policy_alignment or report.has_suspicious_last_overlay_timestamp
            else StatusColor.SUCCESS
        )
        if report.total_video_frames <= 0 and report.total_overlay_frames > 0:
            color = StatusColor.WARNING
        if report.alignment_basis == "sequence":
            details = f"sequence {report.sequence_matches}"
            label = "Sequence"
        else:
            details = f"exact {report.exact_matches}, tol {report.tolerance_text}"
            label = "Timestamp"
        self._alignment_label.setText(
            f"{label}: {prefix} {report.policy_matches}/{report.total_overlay_frames} ({policy_ratio:.0f}%, {details})"
        )
        self._alignment_label.setStyleSheet(f"color: {color.color(self.palette()).name()}; font-weight: 600;")
