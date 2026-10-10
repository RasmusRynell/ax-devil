"""Dialog for adding a live RTSP stream to the workspace."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.form_layout import FormLayout, align_label_columns
from ax_devil.modules.settings.config_manager import (
    ConfigManager,
    expand_environment_reference,
    integer_default,
    is_environment_reference,
)
from ax_devil.modules.workspace.core import ItemResolution, LiveOverlayMode, LiveStreamItem
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from ax_devil.modules.workspace.ui.add_content.analytics_discovery import (
    AnalyticsChoiceLoader,
    list_analytics_data_source_keys,
    list_datahub_topics,
)
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context


def _apply_env_hint(edit: QLineEdit, raw_value: str, resolved_value: str, *, mask: bool = False) -> None:
    """Show the resolved default as placeholder text with its raw env binding.

    When the user leaves the field empty the caller should fall back to
    *resolved_value*.  If the value came from an env var, the placeholder
    shows e.g. ``"172.20.1.1 (via $AX_DEVIL_TARGET_ADDR)"``.  Otherwise it
    just shows the resolved value itself.

    Set *mask* for password fields so the value displays as asterisks.
    """
    if not resolved_value:
        return
    display = "*" * len(resolved_value) if mask else resolved_value
    if is_environment_reference(raw_value):
        edit.setPlaceholderText(f"{display}  (via {raw_value})")
    else:
        edit.setPlaceholderText(display)


class AddLiveStreamDialog(BaseDialog):
    """Dialog for adding a live RTSP stream with optional overlay metadata.

    Empty connection fields take the configured default as written, so a ``$VARIABLE`` default stays a reference in the
    resulting Live Stream Item.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, title="Add Live Stream")
        self._result: ItemResolution | None = None
        self._context = default_resolution_context()
        self._intake = self._context.intake
        self._config = ConfigManager()
        self._raw_defaults, self._defaults = self._load_defaults()
        self._setup_form()
        self._ok_button, _cancel_button = self.add_standard_buttons()
        self._connect_validation()
        self._update_validation()

    def _load_defaults(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return raw and resolved defaults from config."""
        raw_defaults = self._config.get_raw("defaults", {}) or {}
        defaults = self._config.get("defaults", {}) or {}
        return (
            {
                "device": raw_defaults.get("device", {}) or {},
                "live_stream": raw_defaults.get("live_stream", {}) or {},
            },
            {
                "device": defaults.get("device", {}) or {},
                "live_stream": defaults.get("live_stream", {}) or {},
            },
        )

    def _setup_form(self) -> None:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)

        raw_device_defaults = self._raw_defaults["device"]
        raw_live_defaults = self._raw_defaults["live_stream"]
        raw_mqtt_defaults = raw_live_defaults.get("analytics-mqtt", {}) or {}
        device_defaults = self._defaults["device"]
        live_defaults = self._defaults["live_stream"]
        rtsp_defaults = live_defaults.get("rtsp", {}) or {}
        mqtt_defaults = live_defaults.get("analytics-mqtt", {}) or {}
        websocket_defaults = live_defaults.get("analytics-websocket", {}) or {}
        self._mqtt_device_protocol_default = str(mqtt_defaults.get("device_api_protocol", "https")).lower()
        self._websocket_device_protocol_default = str(websocket_defaults.get("device_api_protocol", "https")).lower()

        connection_layout = FormLayout()

        self._name_edit = QLineEdit()
        connection_layout.addRow("Display Name", self._name_edit)

        hint = _apply_env_hint

        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("e.g. 192.168.1.100")
        hint(self._host_edit, str(raw_device_defaults.get("host", "")), device_defaults.get("host", ""))
        self._host_edit.textChanged.connect(self._on_host_changed)
        connection_layout.addRow("Host", self._host_edit)

        self._username_edit = QLineEdit()
        hint(
            self._username_edit,
            str(raw_device_defaults.get("username", "")),
            device_defaults.get("username", ""),
        )
        connection_layout.addRow("Username", self._username_edit)

        self._password_edit = QLineEdit()
        hint(
            self._password_edit,
            str(raw_device_defaults.get("password", "")),
            device_defaults.get("password", ""),
            mask=True,
        )
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        connection_layout.addRow("Password", self._password_edit)

        self._camera_head_edit = QLineEdit()
        self._camera_head_edit.setText(str(integer_default(rtsp_defaults.get("camera_head"), 1)))
        self._camera_head_edit.setValidator(QIntValidator(1, 9999, self))
        connection_layout.addRow("Camera Head", self._camera_head_edit)

        self._resolution_edit = QLineEdit()
        self._resolution_edit.setText(rtsp_defaults.get("resolution", "1280x720"))
        connection_layout.addRow("Resolution", self._resolution_edit)

        self._stream_url_edit = QLineEdit()
        self._stream_url_edit.setPlaceholderText("Optional — overrides auto-built URL")
        connection_layout.addRow("Custom Stream URL", self._stream_url_edit)

        layout.addLayout(connection_layout)

        overlay_group = QGroupBox("Overlay")
        overlay_layout = FormLayout(overlay_group)

        self._overlay_mode_combo = QComboBox()
        for mode in LiveOverlayMode:
            self._overlay_mode_combo.addItem(mode.display_name, mode.value)
        self._overlay_mode_combo.currentIndexChanged.connect(self._on_overlay_mode_changed)
        overlay_layout.addRow("Overlay Mode", self._overlay_mode_combo)

        self._handler_combo = QComboBox()
        self._handler_combo.addItem("(None)", None)
        self._populate_handler_types()
        overlay_layout.addRow("Handler Type", self._handler_combo)

        self._mqtt_device_protocol_combo = QComboBox()
        self._mqtt_device_protocol_combo.addItems(["https", "http"])
        self._mqtt_device_protocol_combo.setToolTip("Protocol used to reach the device's API (HTTPS or HTTP).")
        overlay_layout.addRow("Device Protocol", self._mqtt_device_protocol_combo)

        # One page per mode, in combo order; the stack is sized for the largest page. The None page explains every
        # mode, and each other page starts with its own description.
        transport_pages = {mode: QWidget() for mode in LiveOverlayMode}
        transport_forms = {mode: FormLayout(page) for mode, page in transport_pages.items()}
        for mode, transport_form in transport_forms.items():
            transport_form.setContentsMargins(0, 0, 0, 0)
            descriptions = [mode.description]
            if mode is LiveOverlayMode.NONE:
                descriptions += [
                    f"{other.display_name}: {other.description}" for other in LiveOverlayMode if other != mode
                ]
            for description in descriptions:
                label = QLabel(description)
                label.setWordWrap(True)
                transport_form.addRow(label)
        mqtt_layout = transport_forms[LiveOverlayMode.MQTT]

        self._mqtt_host_edit = QLineEdit()
        hint(
            self._mqtt_host_edit,
            str(raw_mqtt_defaults.get("broker_host", "")),
            mqtt_defaults.get("broker_host", ""),
        )
        mqtt_layout.addRow("MQTT Host", self._mqtt_host_edit)

        self._mqtt_username_edit = QLineEdit()
        hint(
            self._mqtt_username_edit,
            str(raw_mqtt_defaults.get("broker_username", "")),
            mqtt_defaults.get("broker_username", ""),
        )
        mqtt_layout.addRow("MQTT Username", self._mqtt_username_edit)

        self._mqtt_password_edit = QLineEdit()
        hint(
            self._mqtt_password_edit,
            str(raw_mqtt_defaults.get("broker_password", "")),
            mqtt_defaults.get("broker_password", ""),
            mask=True,
        )
        self._mqtt_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        mqtt_layout.addRow("MQTT Password", self._mqtt_password_edit)

        self._mqtt_port_spin = QSpinBox()
        self._mqtt_port_spin.setRange(1, 65535)
        self._mqtt_port_spin.setValue(integer_default(mqtt_defaults.get("broker_port"), 1883))
        mqtt_layout.addRow("MQTT Port", self._mqtt_port_spin)

        self._data_source_choice = AnalyticsChoice(
            AnalyticsChoiceLoader(list_analytics_data_source_keys, "data sources", self),
            str(mqtt_defaults.get("data_source_key", "")),
        )
        mqtt_layout.addRow("Data Source", self._data_source_choice)

        websocket_layout = transport_forms[LiveOverlayMode.WEBSOCKET]

        self._websocket_topic_choice = AnalyticsChoice(
            AnalyticsChoiceLoader(list_datahub_topics, "DataHub topics", self),
            str(websocket_defaults.get("topic", "")),
        )
        websocket_layout.addRow("DataHub Topic", self._websocket_topic_choice)

        self._websocket_channel_id_spin = QSpinBox()
        self._websocket_channel_id_spin.setRange(1, 9999)
        self._websocket_channel_id_spin.setValue(integer_default(websocket_defaults.get("channel_id"), 1))
        self._websocket_channel_id_spin.setToolTip("Instance of the DataHub topic to subscribe to. Most devices use 1.")
        websocket_layout.addRow("Topic Channel", self._websocket_channel_id_spin)

        self._transport_choices = {
            LiveOverlayMode.MQTT: self._data_source_choice,
            LiveOverlayMode.WEBSOCKET: self._websocket_topic_choice,
        }
        self._transport_pages = QStackedWidget()
        for mode in LiveOverlayMode:
            self._transport_pages.addWidget(transport_pages[mode])
        overlay_layout.addRow(self._transport_pages)
        align_label_columns(overlay_layout, *transport_forms.values())

        self._host_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._username_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._password_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._mqtt_device_protocol_combo.currentTextChanged.connect(self._invalidate_discovery_for_connection_change)

        layout.addWidget(overlay_group)

        self._validation_label = QLabel()
        self._validation_label.setWordWrap(True)
        self._validation_label.setStyleSheet("color: palette(placeholder-text);")
        # Reserve the line up front so a message appearing later never makes the content scroll.
        self._validation_label.setMinimumHeight(self._validation_label.fontMetrics().lineSpacing())
        layout.addWidget(self._validation_label)
        layout.addStretch(1)

        self.add_content_widget(form)

        self._apply_configured_defaults(rtsp_defaults, mqtt_defaults, websocket_defaults, live_defaults)
        self._on_host_changed(self._host_edit.text())

    def _apply_configured_defaults(
        self,
        rtsp_defaults: dict[str, Any],
        mqtt_defaults: dict[str, Any],
        websocket_defaults: dict[str, Any],
        live_defaults: dict[str, Any],
    ) -> None:
        try:
            configured_mode = LiveOverlayMode.from_value(str(live_defaults.get("overlay_source", "none")))
        except ValueError:
            configured_mode = LiveOverlayMode.NONE
        configured_handler = None
        if configured_mode is LiveOverlayMode.RTSP:
            configured_handler = rtsp_defaults.get("data_stream_handler")
        elif configured_mode is LiveOverlayMode.MQTT:
            configured_handler = mqtt_defaults.get("data_stream_handler")
            if self._mqtt_device_protocol_default in {"https", "http"}:
                self._mqtt_device_protocol_combo.setCurrentText(self._mqtt_device_protocol_default)
        elif configured_mode is LiveOverlayMode.WEBSOCKET:
            configured_handler = websocket_defaults.get("data_stream_handler")
            if self._websocket_device_protocol_default in {"https", "http"}:
                self._mqtt_device_protocol_combo.setCurrentText(self._websocket_device_protocol_default)

        if configured_handler is not None and self._handler_combo.findData(str(configured_handler)) >= 0:
            self._set_overlay_mode(configured_mode)
            self._set_handler_selection(str(configured_handler))
        else:
            self._set_overlay_mode(LiveOverlayMode.NONE)
            self._handler_combo.setCurrentIndex(0)

        self._on_overlay_mode_changed(self._overlay_mode_combo.currentIndex())

    def _set_overlay_mode(self, mode: LiveOverlayMode) -> None:
        index = self._overlay_mode_combo.findData(mode.value)
        self._overlay_mode_combo.setCurrentIndex(index if index >= 0 else 0)

    def _set_handler_selection(self, handler_type: str) -> None:
        index = self._handler_combo.findData(handler_type)
        self._handler_combo.setCurrentIndex(index if index >= 0 else 0)

    def _text_or_default(self, edit: QLineEdit, default: str) -> str:
        """Return user text if entered, otherwise the config default."""
        text = edit.text().strip()
        return text if text else default

    def _on_host_changed(self, text: str) -> None:
        host = text.strip() or str(self._raw_defaults["device"].get("host") or "")
        if not self._name_edit.text().strip():
            self._name_edit.setPlaceholderText(expand_environment_reference(host))

    def _on_overlay_mode_changed(self, index: int) -> None:
        """Show the selected mode's transport page and enable the fields it uses."""
        mode = LiveOverlayMode.from_value(str(self._overlay_mode_combo.currentData()))
        if mode is LiveOverlayMode.MQTT and self._mqtt_device_protocol_default in {"https", "http"}:
            self._mqtt_device_protocol_combo.setCurrentText(self._mqtt_device_protocol_default)
        elif mode is LiveOverlayMode.WEBSOCKET and self._websocket_device_protocol_default in {"https", "http"}:
            self._mqtt_device_protocol_combo.setCurrentText(self._websocket_device_protocol_default)

        self._handler_combo.setEnabled(mode.requires_handler)
        self._mqtt_device_protocol_combo.setEnabled(mode in self._transport_choices)
        self._transport_pages.setCurrentIndex(index)

        self._invalidate_discovery_for_connection_change()
        choice = self._transport_choices.get(mode)
        if choice is not None and not choice.loader.loaded:
            choice.loader.load()

    def _current_device_connection(self) -> tuple[str, str, str, str]:
        """Return the device connection discovery uses; ``$VARIABLE`` references are expanded for the request only."""
        device_defaults = self._defaults["device"]
        return (
            expand_environment_reference(self._text_or_default(self._host_edit, device_defaults.get("host", ""))),
            expand_environment_reference(
                self._text_or_default(self._username_edit, device_defaults.get("username", ""))
            ),
            expand_environment_reference(
                self._text_or_default(self._password_edit, device_defaults.get("password", ""))
            ),
            self._mqtt_device_protocol_combo.currentText(),
        )

    def _invalidate_discovery_for_connection_change(self) -> None:
        connection = self._current_device_connection()
        self._data_source_choice.loader.set_connection(connection)
        self._websocket_topic_choice.loader.set_connection(connection)

    def _populate_handler_types(self) -> None:
        """Populate handler combo from Workspace live overlay decoder options."""
        for decoder in self._intake.live_overlay_decoder_options():
            self._handler_combo.addItem(decoder.display_name, decoder.handler_type)

    def _connect_validation(self) -> None:
        """Re-check the form whenever any field changes."""
        for edit in self.findChildren(QLineEdit):
            edit.textChanged.connect(self._update_validation)
        for combo in self.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self._update_validation)
        for spin in self.findChildren(QSpinBox):
            spin.valueChanged.connect(self._update_validation)

    def _update_validation(self) -> None:
        """Show what still needs fixing and enable OK only for a complete form."""
        try:
            self._build_result()
        except ValueError as exc:
            message = str(exc)
        else:
            message = ""
        self._validation_label.setText(message)
        self._ok_button.setEnabled(not message)

    def _build_result(self) -> ItemResolution:
        """Return the stream item the form describes, resolved, or raise ``ValueError`` naming what to fix."""
        raw_device_defaults = self._raw_defaults["device"]
        raw_mqtt_defaults = self._raw_defaults["live_stream"].get("analytics-mqtt", {}) or {}

        host = self._text_or_default(self._host_edit, str(raw_device_defaults.get("host") or ""))
        if not expand_environment_reference(host):
            raise ValueError("Enter the device host.")

        mode = LiveOverlayMode.from_value(str(self._overlay_mode_combo.currentData()))
        # The handler combo keeps its selection while disabled; only modes that decode overlays use it.
        handler_type: str | None = self._handler_combo.currentData() if mode.requires_handler else None

        if mode.requires_handler and not handler_type:
            raise ValueError(f"Select a handler type for {mode.display_name} overlays.")

        camera_head = self._camera_head_edit.text().strip() or "1"
        if not camera_head.isdigit():
            raise ValueError("Camera Head must be a positive integer.")

        analytics_data_source_key = self._data_source_choice.combo.currentData()
        websocket_topic_value = self._websocket_topic_choice.combo.currentData()
        websocket_topic = str(websocket_topic_value) if isinstance(websocket_topic_value, str) else ""

        item = LiveStreamItem(
            label=self._name_edit.text().strip(),
            host=host,
            username=self._text_or_default(self._username_edit, str(raw_device_defaults.get("username") or "")),
            password=self._text_or_default(self._password_edit, str(raw_device_defaults.get("password") or "")),
            camera_head=int(camera_head),
            resolution=self._resolution_edit.text().strip() or "1280x720",
            stream_url=self._stream_url_edit.text().strip() or None,
            overlay_mode=mode,
            handler_type=handler_type,
            mqtt_host=self._text_or_default(self._mqtt_host_edit, str(raw_mqtt_defaults.get("broker_host") or "")),
            mqtt_port=self._mqtt_port_spin.value(),
            mqtt_username=self._text_or_default(
                self._mqtt_username_edit, str(raw_mqtt_defaults.get("broker_username") or "")
            ),
            mqtt_password=self._text_or_default(
                self._mqtt_password_edit, str(raw_mqtt_defaults.get("broker_password") or "")
            ),
            analytics_data_source_key=(str(analytics_data_source_key) if analytics_data_source_key is not None else ""),
            device_api_protocol=self._mqtt_device_protocol_combo.currentText(),
            websocket_topic=websocket_topic,
            websocket_channel_id=self._websocket_channel_id_spin.value(),
        )
        resolution = ItemResolution.of(item, self._context)
        if resolution.error is not None:
            raise ValueError(str(resolution.error))
        return resolution

    def accept(self) -> None:
        """Add the stream when the form is complete; otherwise keep the dialog open with the reason shown."""
        try:
            self._result = self._build_result()
        except ValueError as exc:
            self._validation_label.setText(str(exc))
            return
        super().accept()

    def get_result(self) -> ItemResolution | None:
        """Return the resolved Live Stream Item, or None if the dialog was cancelled."""
        return self._result

    def cleanup(self) -> None:
        """Release asynchronous dialog resources."""
        self._data_source_choice.cleanup()
        self._websocket_topic_choice.cleanup()
