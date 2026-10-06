"""Global application settings dialog."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.configuration_editor import ConfigurationEditor
from ax_devil.modules.settings.configuration_preferences import (
    CONNECTION_SECTIONS,
    STORAGE_SECTION,
    ConfigField,
    ConfigSection,
    apply_preferences,
    field_value,
)
from ax_devil.modules.settings.graphics_acceleration import GraphicsAcceleration
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.overlay_preferences import OverlayPreference
from ax_devil.modules.settings.playback_settings import VideoCacheBudget
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog

RESTART_MARK = "(requires restart)"
"""Suffix on every setting that takes effect only after restarting the app."""


def _restart_label(text: str) -> str:
    """Return *text* marked as a setting that takes effect after a restart."""
    return f"{text} {RESTART_MARK}"


class SettingsDialog(BaseDialog):
    """Dialog for editing global application settings.

    Reads a snapshot of ``GlobalSettings`` on open. Changes are applied
    only when the user clicks **OK** (apply + close) or **Apply** (apply,
    stay open).  **Cancel** discards uncommitted changes. Settings that
    only take effect after a restart carry ``RESTART_MARK`` in their label.
    The separate shortcut editor applies its changes on its own **OK**.
    """

    def __init__(self, parent: Optional[QWidget] = None, *, shortcut_manager: ShortcutManager | None = None) -> None:
        super().__init__(parent=parent, title="Settings", modal=True, scroll_content=False)
        self._logger = get_logger(__name__)
        self._settings = GlobalSettings()
        self._config = ConfigManager()
        self._shortcut_manager = shortcut_manager
        self._configuration_editors: dict[ConfigField, ConfigurationEditor] = {}
        self._setup_content()
        self._setup_buttons()
        self._load_from_settings()
        self._logger.debug("SettingsDialog initialized")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _setup_content(self) -> None:
        """Build the settings form."""
        self._tabs = QTabWidget()
        self._page_layouts: list[QVBoxLayout] = []
        layout = self._add_page("General")

        appearance_group = QGroupBox("Appearance")
        appearance_layout = FormLayout(appearance_group)
        self._theme_combo = QComboBox()
        for theme in ThemeMode:
            self._theme_combo.addItem(theme.label, theme.value)
        appearance_layout.addRow("Theme", self._theme_combo)
        self._text_size_combo = QComboBox()
        for size in TextSize:
            self._text_size_combo.addItem(size.label, size.value)
        appearance_layout.addRow("Text size", self._text_size_combo)
        self._custom_frame = QCheckBox(_restart_label("Use the app title bar"))
        appearance_layout.addRow(self._custom_frame)
        self._acceleration_combo = QComboBox()
        for mode in GraphicsAcceleration:
            self._acceleration_combo.addItem(mode.label, mode.value)
        appearance_layout.addRow(_restart_label("Graphics acceleration"), self._acceleration_combo)
        self._acceleration_description = QLabel()
        self._acceleration_description.setWordWrap(True)
        appearance_layout.addRow(self._acceleration_description)
        override = self._settings.graphics_acceleration_override
        if override is not None:
            override_label = QLabel(f"Widget presentation is overridden by QT_WIDGETS_RHI={override}.")
            override_label.setWordWrap(True)
            appearance_layout.addRow(override_label)
        self._acceleration_combo.currentIndexChanged.connect(self._update_acceleration_description)
        layout.addWidget(appearance_group)

        playback_group = QGroupBox("Playback")
        playback_layout = FormLayout(playback_group)
        self._video_cache_mode = QComboBox()
        self._video_cache_mode.addItem(VideoCacheBudget.AUTO_LABEL, True)
        self._video_cache_mode.addItem(VideoCacheBudget.MANUAL_LABEL, False)
        playback_layout.addRow(VideoCacheBudget.LABEL, self._video_cache_mode)
        self._video_cache_spin = QDoubleSpinBox()
        self._video_cache_spin.setRange(VideoCacheBudget.MIN_MIB / 1024, VideoCacheBudget.MAX_MIB / 1024)
        self._video_cache_spin.setDecimals(2)
        self._video_cache_spin.setSuffix(" GiB")
        self._video_cache_spin.setSingleStep(0.25)
        self._video_cache_spin.setValue(1.0)
        self._video_cache_manual_label = QLabel("Total allowance")
        playback_layout.addRow(self._video_cache_manual_label, self._video_cache_spin)
        description = QLabel(VideoCacheBudget.DESCRIPTION)
        description.setWordWrap(True)
        playback_layout.addRow(description)
        self._video_cache_mode.currentIndexChanged.connect(self._update_video_cache_controls)
        layout.addWidget(playback_group)

        overlay_group = QGroupBox("Video Overlays")
        overlay_layout = QVBoxLayout(overlay_group)
        self._overlay_checkboxes: dict[OverlayPreference, QCheckBox] = {}
        for preference in OverlayPreference:
            checkbox = QCheckBox(preference.label)
            checkbox.setToolTip(preference.description)
            overlay_layout.addWidget(checkbox)
            self._overlay_checkboxes[preference] = checkbox

        layout.addWidget(overlay_group)

        shortcuts = QPushButton("Keyboard shortcuts…")
        shortcuts.setEnabled(self._shortcut_manager is not None)
        shortcuts.clicked.connect(self._edit_shortcuts)
        layout.addWidget(shortcuts)
        self._add_note(
            layout,
            "Shortcut changes apply when you click OK in the shortcut editor, even if you cancel Settings.",
        )

        streams = self._add_page("Stream defaults")
        self._add_note(
            streams,
            "These defaults prefill Add Live Stream. Each stream can override them. "
            "Existing viewers keep their settings. "
            "You can enter $VARIABLE_NAME to keep using an environment variable.",
        )
        for section in CONNECTION_SECTIONS:
            self._add_config_section(streams, section)
        storage = self._add_page("Storage")
        self._add_note(storage, "Existing files are not moved.")
        self._add_config_section(storage, STORAGE_SECTION)
        layout = self._add_page("Advanced")
        self._add_note(layout, f"Configuration file: {self._config.config_path}")
        self._add_note(layout, "The configuration file is selected at launch with --config.")
        self._add_note(layout, f"Configuration version: {self._config.get_raw('version')}")
        # A trailing stretch, not top alignment: an aligned layout ignores wrapped-text height and overlaps rows.
        for page_layout in self._page_layouts:
            page_layout.addStretch(1)
        self.add_content_widget(self._tabs, stretch=1)
        apply_rule = QLabel(
            f"Changes in this dialog apply when you click OK or Apply. Settings marked {RESTART_MARK} take effect "
            "the next time the app starts."
        )
        apply_rule.setWordWrap(True)
        self.add_content_widget(apply_rule)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self.add_content_widget(self._status_label)

    def _add_page(self, title: str) -> QVBoxLayout:
        content = QWidget()
        layout = QVBoxLayout(content)
        self._page_layouts.append(layout)
        scroll = ContentScrollArea(content)
        self._tabs.addTab(scroll, title)
        return layout

    def _add_note(self, layout: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)

    def _add_config_section(self, layout: QVBoxLayout, section: ConfigSection) -> None:
        group = QGroupBox(_restart_label(section.title) if section.requires_restart else section.title)
        form = FormLayout(group)
        for field in section.fields:
            editor = ConfigurationEditor(field, field_value(self._config, field))
            self._configuration_editors[field] = editor
            form.addRow(field.label, editor)
        layout.addWidget(group)

    def _edit_shortcuts(self) -> None:
        if self._shortcut_manager is not None:
            with ShortcutsDialog(self._shortcut_manager, parent=self) as dialog:
                dialog.exec()

    def _setup_buttons(self) -> None:
        """Add OK / Apply / Cancel buttons."""
        self.add_button("OK", self._on_ok, is_default=True)
        self.add_button("Apply", self._on_apply)
        self.add_button("Cancel", self.reject)

    # ------------------------------------------------------------------
    # Data flow
    # ------------------------------------------------------------------

    def _load_from_settings(self) -> None:
        """Populate widgets from current ``GlobalSettings`` values."""
        budget = self._settings.video_cache_budget
        self._video_cache_mode.setCurrentIndex(0 if budget.mib is None else 1)
        self._video_cache_spin.setValue((budget.mib or VideoCacheBudget.FALLBACK_MIB) / 1024)
        self._update_video_cache_controls()
        self._custom_frame.setChecked(self._settings.custom_frame)
        for preference, checkbox in self._overlay_checkboxes.items():
            checkbox.setChecked(self._settings.is_overlay_enabled(preference))
        self._theme_combo.setCurrentIndex(self._theme_combo.findData(self._settings.theme.value))
        self._text_size_combo.setCurrentIndex(self._text_size_combo.findData(self._settings.text_size.value))
        self._acceleration_combo.setCurrentIndex(
            self._acceleration_combo.findData(self._settings.graphics_acceleration.value)
        )
        self._update_acceleration_description()

    def _update_video_cache_controls(self) -> None:
        auto = bool(self._video_cache_mode.currentData())
        self._video_cache_spin.setEnabled(not auto)
        self._video_cache_spin.setVisible(not auto)
        self._video_cache_manual_label.setVisible(not auto)

    def _selected_cache_budget(self) -> VideoCacheBudget:
        if self._video_cache_mode.currentData():
            return VideoCacheBudget()
        return VideoCacheBudget(round(self._video_cache_spin.value() * 1024))

    def _update_acceleration_description(self) -> None:
        mode = GraphicsAcceleration(self._acceleration_combo.currentData())
        self._acceleration_description.setText(mode.description)

    def _build_snapshot(self) -> dict[str, Any]:
        """Build a settings snapshot from the widgets; settings this dialog does not show keep their value."""
        return {
            **self._settings.snapshot(),
            "playback": {"video_cache_total_mib": self._selected_cache_budget().config_value},
            "appearance": {
                "graphics_acceleration": self._acceleration_combo.currentData(),
                "theme": self._theme_combo.currentData(),
                "text_size": self._text_size_combo.currentData(),
                "custom_frame": self._custom_frame.isChecked(),
            },
            "overlay_interaction": OverlayPreference.config_value(
                {preference: checkbox.isChecked() for preference, checkbox in self._overlay_checkboxes.items()}
            ),
        }

    def _apply(self) -> bool:
        """Validate and save all pages, keeping the dialog open on failure."""
        try:
            apply_preferences(
                self._config,
                self._settings,
                self._build_snapshot(),
                {field: editor.text() for field, editor in self._configuration_editors.items()},
            )
        except (ValueError, OSError) as exc:
            self._status_label.setText(f"Could not save settings: {exc}")
            return False
        self._status_label.setText("Settings saved.")
        return True

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        """Apply and close."""
        if self._apply():
            self.accept()

    def _on_apply(self) -> None:
        """Apply and keep dialog open."""
        self._apply()
