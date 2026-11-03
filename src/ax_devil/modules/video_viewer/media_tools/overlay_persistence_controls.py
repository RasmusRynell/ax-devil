"""Runtime controls for sticky overlay persistence settings."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.video_viewer.media_tools.scope_row import ScopeRow
from ax_devil.modules.video_viewer.overlay_persistence import MAX_TIMEOUT_MS, OverlayPersistenceSettings
from ax_devil.modules.video_viewer.view_settings import STICKY_OVERLAYS


class OverlayPersistenceControls(QGroupBox):
    """Interactive controls for adjusting overlay persistence at runtime."""

    settingsChanged = Signal(object)  # Emits OverlayPersistenceSettings snapshots

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_settings: OverlayPersistenceSettings | None = None,
        show_title: bool = True,
    ) -> None:
        super().__init__("Sticky Overlay Settings" if show_title else "", parent)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._show_title = show_title
        self._current_settings = initial_settings if initial_settings is not None else OverlayPersistenceSettings()

        self._enabled_checkbox = QCheckBox("Enable sticky overlays")
        self._enabled_checkbox.setObjectName("overlayEnabledCheckbox")
        self._timeout_checkbox = QCheckBox("Expire after (ms)")
        self._timeout_checkbox.setObjectName("overlayTimeoutCheckbox")
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setObjectName("overlayTimeoutSpin")
        self._opacity_spin = QDoubleSpinBox()
        self._opacity_spin.setObjectName("overlayOpacitySpin")

        self._scope = ScopeRow(STICKY_OVERLAYS, self._current_settings, adopt=self._adopt)

        self._build_ui()
        self.apply_settings(self._current_settings)
        self._connect_signals()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(Space.S)
        if not self._show_title:
            layout.setContentsMargins(0, 0, 0, 0)
            self.setFlat(True)
            self.setStyleSheet("QGroupBox { border: none; margin-top: 0; padding: 0; }")

        layout.addWidget(self._enabled_checkbox)

        timeout_row = QHBoxLayout()
        timeout_row.setSpacing(Space.S)

        self._timeout_spin.setRange(0, MAX_TIMEOUT_MS)
        self._timeout_spin.setSingleStep(1)

        timeout_row.addWidget(self._timeout_checkbox)
        timeout_row.addWidget(self._timeout_spin)
        timeout_row.addStretch(1)
        layout.addLayout(timeout_row)

        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(Space.S)
        opacity_label = QLabel("Reused overlay opacity:")

        self._opacity_spin.setRange(0.0, 1.0)
        self._opacity_spin.setSingleStep(0.05)
        self._opacity_spin.setDecimals(2)

        opacity_row.addWidget(opacity_label)
        opacity_row.addWidget(self._opacity_spin)
        opacity_row.addStretch(1)
        layout.addLayout(opacity_row)

        layout.addWidget(self._scope)

    def _connect_signals(self) -> None:
        self._enabled_checkbox.toggled.connect(self._on_enabled_toggled)
        self._timeout_checkbox.toggled.connect(self._on_timeout_toggled)
        self._timeout_spin.valueChanged.connect(lambda _: self._emit_settings())
        self._opacity_spin.valueChanged.connect(lambda _: self._emit_settings())

    def _adopt(self, settings: OverlayPersistenceSettings) -> None:
        """Show settings that Apply to all chose in another view, and tell the owner."""
        self.apply_settings(settings)
        self.settingsChanged.emit(settings)

    def apply_settings(self, settings: OverlayPersistenceSettings) -> None:
        """Populate controls from the provided settings without emitting."""
        self._current_settings = settings

        self._enabled_checkbox.blockSignals(True)
        self._timeout_checkbox.blockSignals(True)
        self._timeout_spin.blockSignals(True)
        self._opacity_spin.blockSignals(True)
        try:
            self._enabled_checkbox.setChecked(settings.enabled)
            timeout_enabled = settings.timeout_ms is not None
            self._timeout_checkbox.setChecked(timeout_enabled)
            if settings.timeout_ms is not None:
                self._timeout_spin.setValue(int(settings.timeout_ms))
            self._opacity_spin.setValue(settings.opacity)
        finally:
            self._enabled_checkbox.blockSignals(False)
            self._timeout_checkbox.blockSignals(False)
            self._timeout_spin.blockSignals(False)
            self._opacity_spin.blockSignals(False)

        self._refresh_control_states()
        self._scope.set_value(settings)

    def current_settings(self) -> OverlayPersistenceSettings:
        """Return the latest settings represented by the controls."""
        return self._current_settings

    def _on_enabled_toggled(self, enabled: bool) -> None:
        self._refresh_control_states()
        self._emit_settings()

    def _on_timeout_toggled(self, checked: bool) -> None:
        self._refresh_control_states()
        self._emit_settings()

    def _refresh_control_states(self) -> None:
        enabled = self._enabled_checkbox.isChecked()
        timeout_enabled = enabled and self._timeout_checkbox.isChecked()

        self._timeout_checkbox.setEnabled(enabled)
        self._timeout_spin.setEnabled(timeout_enabled)
        self._opacity_spin.setEnabled(enabled)

    def _emit_settings(self) -> None:
        updated = OverlayPersistenceSettings(
            enabled=self._enabled_checkbox.isChecked(),
            timeout_ms=self._timeout_spin.value() if self._timeout_checkbox.isChecked() else None,
            opacity=self._opacity_spin.value(),
        )

        if updated == self._current_settings:
            return

        self._current_settings = updated
        self._scope.set_value(updated)
        self.settingsChanged.emit(updated)
