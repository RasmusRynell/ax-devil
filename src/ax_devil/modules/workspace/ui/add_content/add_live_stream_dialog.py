"""Dialog for adding a live RTSP stream to the workspace."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ax_devil.core.cancellation import NEVER
from ax_devil.core.device_endpoint import DeviceEndpoint, DeviceProtocol
from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.form_layout import FormLayout, align_label_columns
from ax_devil.modules.chrome.tokens import reserve_line
from ax_devil.modules.settings.config_manager import (
    ConfigManager,
    builtin_default,
    default_protocol,
    expand_environment_reference,
    integer_default,
)
from ax_devil.modules.workspace.core import ItemResolution, LiveOverlayMode, LiveStreamItem
from ax_devil.modules.workspace.ui.add_content.analytics_discovery import DeviceConnection
from ax_devil.modules.workspace.ui.add_content.live_stream_defaults import configured_overlay, connection_numbers
from ax_devil.modules.workspace.ui.add_content.live_transport_pages import TransportPages, apply_env_hint
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context


class AddLiveStreamDialog(BaseDialog):
    """Dialog for adding a live RTSP stream with optional overlay metadata.

    Empty connection fields take the configured default as written, so a ``$VARIABLE`` default stays a reference in the
    resulting Live Stream Item.
    """

    def __init__(self, parent: QWidget | None = None, *, existing: LiveStreamItem | None = None) -> None:
        self._existing = existing
        self._restoring = existing is not None
        super().__init__(parent, title="Edit Live Stream" if existing is not None else "Add Live Stream")
        self._result: ItemResolution | None = None
        self._context = default_resolution_context()
        self._intake = self._context.intake
        self._config = ConfigManager()
        self._raw_defaults, self._defaults = self._load_defaults()
        self._setup_form()
        self._ok_button, _cancel_button = self.add_standard_buttons()
        if existing is not None:
            self._restore(existing)
            self._ok_button.setText("Save")
        self._restoring = False
        self._connect_validation()
        self._update_validation()

    def _restore(self, item: LiveStreamItem) -> None:
        """Restore raw saved values, including inactive transport fields and empty credentials."""
        for edit, value in (
            (self._name_edit, item.label),
            (self._host_edit, item.host),
            (self._username_edit, item.username),
            (self._password_edit, item.password),
            (self._camera_head_edit, str(item.camera_head)),
            (self._resolution_edit, item.resolution),
            (self._stream_url_edit, item.stream_url or ""),
            (self._transports.mqtt_host_edit, item.mqtt_host),
            (self._transports.mqtt_username_edit, item.mqtt_username),
            (self._transports.mqtt_password_edit, item.mqtt_password),
        ):
            edit.setPlaceholderText("")
            edit.setText(value)
        self._transports.mqtt_port_edit.setText(str(item.mqtt_port))
        self._transports.websocket_channel_id_edit.setText(str(item.websocket_channel_id))
        self._set_overlay_mode(item.overlay_mode)
        if item.handler_type is not None and self._handler_combo.findData(item.handler_type) < 0:
            self._handler_combo.addItem(f"{item.handler_type} (unavailable)", item.handler_type)
        self._set_handler_selection(item.handler_type or "")
        self._protocol_combo.setCurrentIndex(self._protocol_combo.findData(item.device_api_protocol.value))
        self._transports.data_source_choice.restore_selection(item.analytics_data_source_key)
        self._transports.websocket_topic_choice.restore_selection(item.websocket_topic)
        self._on_overlay_mode_changed(self._overlay_mode_combo.currentIndex())

    def _load_defaults(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return raw and resolved defaults from config."""
        raw_defaults = self._config.get_raw("defaults")
        defaults = self._config.get("defaults")
        return (
            {"device": raw_defaults["device"], "live_stream": raw_defaults["live_stream"]},
            {"device": defaults["device"], "live_stream": defaults["live_stream"]},
        )

    def _setup_form(self) -> None:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)

        raw_device_defaults = self._raw_defaults["device"]
        raw_live_defaults = self._raw_defaults["live_stream"]
        raw_mqtt_defaults = raw_live_defaults["analytics-mqtt"]
        live_defaults = self._defaults["live_stream"]
        rtsp_defaults = live_defaults["rtsp"]
        mqtt_defaults = live_defaults["analytics-mqtt"]
        websocket_defaults = live_defaults["analytics-websocket"]

        connection_layout = FormLayout()

        self._name_edit = QLineEdit()
        connection_layout.addRow("Display Name", self._name_edit)

        hint = apply_env_hint

        self._host_edit = QLineEdit()
        self._host_edit.setPlaceholderText("e.g. 192.168.1.100")
        hint(self._host_edit, str(raw_device_defaults.get("host", "")))
        self._host_edit.textChanged.connect(self._on_host_changed)
        connection_layout.addRow("Host", self._host_edit)

        self._username_edit = QLineEdit()
        hint(
            self._username_edit,
            str(raw_device_defaults.get("username", "")),
        )
        connection_layout.addRow("Username", self._username_edit)

        self._password_edit = QLineEdit()
        hint(
            self._password_edit,
            str(raw_device_defaults.get("password", "")),
            mask=True,
        )
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        connection_layout.addRow("Password", self._password_edit)

        configured = default_protocol(self._config)
        self._protocol_combo = QComboBox()
        self._protocol_combo.addItem(f"Default ({configured.label})", None)
        for protocol in DeviceProtocol:
            self._protocol_combo.addItem(protocol.label, protocol.value)
        self._protocol_combo.setToolTip("Protocol used to reach the device's web API.")
        connection_layout.addRow("Protocol", self._protocol_combo)

        self._camera_head_edit = QLineEdit()
        self._camera_head_edit.setText(
            str(integer_default(rtsp_defaults["camera_head"], "defaults.live_stream.rtsp.camera_head"))
        )
        self._camera_head_edit.setValidator(QIntValidator(1, 9999, self))
        connection_layout.addRow("Camera Head", self._camera_head_edit)

        self._resolution_edit = QLineEdit()
        self._resolution_edit.setText(rtsp_defaults["resolution"])
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

        self._transports = TransportPages(raw_mqtt_defaults, mqtt_defaults, websocket_defaults, self)
        overlay_layout.addRow(self._transports)
        align_label_columns(overlay_layout, *self._transports.forms)

        self._host_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._username_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._password_edit.textChanged.connect(self._invalidate_discovery_for_connection_change)
        self._protocol_combo.currentIndexChanged.connect(self._invalidate_discovery_for_connection_change)

        layout.addWidget(overlay_group)

        self._validation_label = QLabel()
        self._validation_label.setWordWrap(True)
        self._validation_label.setStyleSheet("color: palette(placeholder-text);")
        # Reserve the line up front so a message appearing later never makes the content scroll.
        reserve_line(self._validation_label)
        layout.addWidget(self._validation_label)
        layout.addStretch(1)

        self.add_content_widget(form)

        if self._existing is None:
            self._apply_configured_defaults(live_defaults)
        self._on_host_changed(self._host_edit.text())

    def _apply_configured_defaults(self, live_defaults: dict[str, Any]) -> None:
        installed = {decoder.handler_type for decoder in self._intake.live_overlay_decoder_options()}
        mode, handler = configured_overlay(live_defaults, installed)
        self._set_overlay_mode(mode)
        self._set_handler_selection(handler or "")
        self._on_overlay_mode_changed(self._overlay_mode_combo.currentIndex())

    def _set_overlay_mode(self, mode: LiveOverlayMode) -> None:
        index = self._overlay_mode_combo.findData(mode.value)
        self._overlay_mode_combo.setCurrentIndex(index if index >= 0 else 0)

    def _set_handler_selection(self, handler_type: str) -> None:
        index = self._handler_combo.findData(handler_type)
        self._handler_combo.setCurrentIndex(index if index >= 0 else 0)

    def _text_or_default(self, edit: QLineEdit, default: str, *, saved: str | None = None) -> str:
        """Keep saved text, trim changed connection fields and apply defaults only when adding."""
        text = edit.text() if self._existing is not None else edit.text().strip()
        if saved is not None and text != saved:
            text = text.strip()
        return text if text or self._existing is not None else default

    def _on_host_changed(self, text: str) -> None:
        host = text.strip() or str(self._raw_defaults["device"].get("host") or "")
        if not self._name_edit.text().strip():
            self._name_edit.setPlaceholderText(expand_environment_reference(host))

    def _on_overlay_mode_changed(self, index: int) -> None:
        """Show the selected mode's transport page and enable the fields it uses."""
        mode = LiveOverlayMode.from_value(str(self._overlay_mode_combo.currentData()))
        self._handler_combo.setEnabled(mode.requires_handler)
        self._transports.setCurrentIndex(index)

        self._invalidate_discovery_for_connection_change()
        choice = self._transports.choices.get(mode)
        if not self._restoring and choice is not None and not choice.loader.loaded:
            choice.loader.load()

    def _protocol(self) -> DeviceProtocol:
        """Return the chosen protocol; Default means the configured one."""
        chosen = self._protocol_combo.currentData()
        return DeviceProtocol(chosen) if chosen else default_protocol(self._config)

    def _current_device_connection(self) -> DeviceConnection:
        """Return the device connection discovery uses; ``$VARIABLE`` references are expanded for the request only."""
        device_defaults = self._defaults["device"]
        device_port, _rtsp_port, timeout_s = connection_numbers(device_defaults, self._existing)
        host = self._text_or_default(
            self._host_edit, device_defaults.get("host", ""), saved=self._existing.host if self._existing else ""
        )
        return DeviceConnection(
            DeviceEndpoint(expand_environment_reference(host), self._protocol(), device_port),
            expand_environment_reference(
                self._text_or_default(self._username_edit, device_defaults.get("username", ""))
            ),
            expand_environment_reference(
                self._text_or_default(self._password_edit, device_defaults.get("password", ""))
            ),
            timeout_s,
        )

    def _invalidate_discovery_for_connection_change(self) -> None:
        connection = self._current_device_connection()
        self._transports.data_source_choice.loader.set_connection(connection)
        self._transports.websocket_topic_choice.loader.set_connection(connection)

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
        raw_mqtt_defaults = self._raw_defaults["live_stream"]["analytics-mqtt"]

        host = self._text_or_default(
            self._host_edit,
            str(raw_device_defaults.get("host") or ""),
            saved=self._existing.host if self._existing else "",
        )
        if not expand_environment_reference(host):
            raise ValueError("Enter the device host.")

        mode = LiveOverlayMode.from_value(str(self._overlay_mode_combo.currentData()))
        # The handler combo keeps its selection while disabled; only modes that decode overlays use it.
        handler_type: str | None = self._handler_combo.currentData() if mode.requires_handler else None

        if mode.requires_handler and not handler_type:
            raise ValueError(f"Select a handler type for {mode.display_name} overlays.")

        camera_head = self._camera_head_edit.text().strip() or str(
            builtin_default("defaults.live_stream.rtsp.camera_head")
        )
        if not camera_head.isdigit():
            raise ValueError("Camera Head must be a positive integer.")

        analytics_data_source_key = self._transports.data_source_choice.combo.currentData()
        websocket_topic_value = self._transports.websocket_topic_choice.combo.currentData()
        websocket_topic = str(websocket_topic_value) if isinstance(websocket_topic_value, str) else ""

        stream_url = self._text_or_default(
            self._stream_url_edit, "", saved=(self._existing.stream_url or "") if self._existing else ""
        )
        saved_empty_url = self._existing is not None and self._existing.stream_url == ""
        device_port, rtsp_port, connection_timeout_s = connection_numbers(self._defaults["device"], self._existing)
        item = LiveStreamItem(
            label=self._name_edit.text().strip(),
            host=host,
            username=self._text_or_default(self._username_edit, str(raw_device_defaults.get("username") or "")),
            password=self._text_or_default(self._password_edit, str(raw_device_defaults.get("password") or "")),
            camera_head=int(camera_head),
            resolution=self._resolution_edit.text().strip() or builtin_default("defaults.live_stream.rtsp.resolution"),
            stream_url=stream_url if stream_url or saved_empty_url else None,
            overlay_mode=mode,
            handler_type=handler_type,
            mqtt_host=self._text_or_default(
                self._transports.mqtt_host_edit,
                str(raw_mqtt_defaults.get("broker_host") or ""),
                saved=self._existing.mqtt_host if self._existing else "",
            ),
            mqtt_port=self._transports.mqtt_port(),
            mqtt_username=self._text_or_default(
                self._transports.mqtt_username_edit, str(raw_mqtt_defaults.get("broker_username") or "")
            ),
            mqtt_password=self._text_or_default(
                self._transports.mqtt_password_edit, str(raw_mqtt_defaults.get("broker_password") or "")
            ),
            analytics_data_source_key=(str(analytics_data_source_key) if analytics_data_source_key is not None else ""),
            websocket_topic=websocket_topic,
            websocket_channel_id=self._transports.websocket_channel_id(),
            device_api_protocol=self._protocol(),
            device_port=device_port,
            rtsp_port=rtsp_port,
            connection_timeout_s=connection_timeout_s,
        )
        if self._existing is not None:
            item = replace(item, id=self._existing.id)
        resolution = ItemResolution.of(item, self._context, NEVER)
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
        self._transports.data_source_choice.cleanup()
        self._transports.websocket_topic_choice.cleanup()
