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

from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.overlay_persistence import MAX_TIMEOUT_MS, OverlayPersistenceSettings
from ax_devil.modules.video_viewer.overlay_persistence_views import overlay_persistence_views

logger = get_logger(__name__)

_MUTED = "color: palette(placeholder-text);"


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
        self._current_settings = (
            initial_settings.copy() if initial_settings is not None else OverlayPersistenceSettings()
        )

        self._enabled_checkbox = QCheckBox("Enable sticky overlays")
        self._enabled_checkbox.setObjectName("overlayEnabledCheckbox")
        self._timeout_checkbox = QCheckBox("Expire after (ms)")
        self._timeout_checkbox.setObjectName("overlayTimeoutCheckbox")
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setObjectName("overlayTimeoutSpin")
        self._opacity_spin = QDoubleSpinBox()
        self._opacity_spin.setObjectName("overlayOpacitySpin")

        self._default_label = QLabel("Default for new views")
        self._default_label.setObjectName("stickyDefaultLabel")
        self._default_label.setStyleSheet(_MUTED)
        TextRole.SMALL.apply(self._default_label)
        self._make_default_button = SmallButton("Make default")
        self._make_default_button.setObjectName("useStickyAsDefaultButton")
        self._make_default_button.setToolTip("Start new viewers and lanes with these settings.")
        self._apply_to_all_button = SmallButton("Apply to all")
        self._apply_to_all_button.setObjectName("applyStickyToAllButton")
        self._apply_to_all_button.setToolTip("Use these settings in every open viewer and lane.")

        self._closed = False
        self._views = overlay_persistence_views()
        self._key = self._views.add(self._current_settings)

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

        scope_row = QHBoxLayout()
        scope_row.setSpacing(Space.S)
        scope_row.addWidget(self._default_label)
        scope_row.addWidget(self._make_default_button)
        scope_row.addStretch(1)
        scope_row.addWidget(self._apply_to_all_button)
        layout.addLayout(scope_row)

    def _connect_signals(self) -> None:
        self._enabled_checkbox.toggled.connect(self._on_enabled_toggled)
        self._timeout_checkbox.toggled.connect(self._on_timeout_toggled)
        self._timeout_spin.valueChanged.connect(lambda _: self._emit_settings())
        self._opacity_spin.valueChanged.connect(lambda _: self._emit_settings())
        self._make_default_button.clicked.connect(self._make_default)
        self._apply_to_all_button.clicked.connect(lambda: self._views.apply_to_all(self._current_settings))
        self._views.changed.connect(self._render_scope)
        self._views.appliedToAll.connect(self._adopt)

    def cleanup(self) -> None:
        """Leave the shared view list and stop listening to it when the owning view closes; repeat calls do nothing."""
        if self._closed:
            return
        self._closed = True
        self._views.discard(self._key)
        self._views.changed.disconnect(self._render_scope)
        self._views.appliedToAll.disconnect(self._adopt)

    def _make_default(self) -> None:
        try:
            self._views.use_as_default(self._current_settings)
        except OSError as exc:
            logger.error(f"Could not save the default sticky overlay settings: {exc}")

    def _adopt(self, settings: OverlayPersistenceSettings) -> None:
        if settings == self._current_settings:
            return
        self.apply_settings(settings)
        self.settingsChanged.emit(settings.copy())

    def _render_scope(self) -> None:
        in_use = self._views.settings_in_use()
        is_default = self._current_settings == self._views.default()
        differs = any(settings != self._current_settings for settings in in_use)
        self._default_label.setVisible(is_default)
        self._make_default_button.setVisible(not is_default)
        self._apply_to_all_button.setText(f"Apply to all {len(in_use)}")
        self._apply_to_all_button.setVisible(differs)
        if self.isVisible():
            self.window().adjustSize()

    def apply_settings(self, settings: OverlayPersistenceSettings) -> None:
        """Populate controls from the provided settings without emitting."""
        snapshot = settings.copy()
        self._current_settings = snapshot

        self._enabled_checkbox.blockSignals(True)
        self._timeout_checkbox.blockSignals(True)
        self._timeout_spin.blockSignals(True)
        self._opacity_spin.blockSignals(True)
        try:
            self._enabled_checkbox.setChecked(snapshot.enabled)
            timeout_enabled = snapshot.timeout_ms is not None
            self._timeout_checkbox.setChecked(timeout_enabled)
            if snapshot.timeout_ms is not None:
                self._timeout_spin.setValue(int(snapshot.timeout_ms))
            self._opacity_spin.setValue(snapshot.opacity)
        finally:
            self._enabled_checkbox.blockSignals(False)
            self._timeout_checkbox.blockSignals(False)
            self._timeout_spin.blockSignals(False)
            self._opacity_spin.blockSignals(False)

        self._refresh_control_states()
        self._views.set_settings(self._key, snapshot)
        self._render_scope()

    def current_settings(self) -> OverlayPersistenceSettings:
        """Return the latest settings represented by the controls."""
        return self._current_settings.copy()

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
        self._views.set_settings(self._key, updated)
        self.settingsChanged.emit(updated.copy())
