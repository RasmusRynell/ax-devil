"""The pages of the Add Live Stream dialog that hold the fields of each live overlay transport."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QLineEdit, QStackedWidget, QWidget

from ax_devil.modules.chrome.form_layout import FormLayout
from ax_devil.modules.settings.config_manager import default_hint, integer_default
from ax_devil.modules.workspace.core import LiveOverlayMode
from ax_devil.modules.workspace.ui.add_content.analytics_choice import AnalyticsChoice
from ax_devil.modules.workspace.ui.add_content.analytics_discovery import (
    AnalyticsChoiceLoader,
    list_analytics_data_source_keys,
    list_datahub_topics,
)


def apply_env_hint(edit: QLineEdit, raw_value: str, *, mask: bool = False) -> None:
    """Show the default *raw_value* stands for as placeholder text; the caller falls back to it for an empty field."""
    if hint := default_hint(raw_value, mask=mask):
        edit.setPlaceholderText(hint)


_HANDLER_SECTIONS = {
    LiveOverlayMode.RTSP: "rtsp",
    LiveOverlayMode.MQTT: "analytics-mqtt",
    LiveOverlayMode.WEBSOCKET: "analytics-websocket",
}


def configured_overlay(live_defaults: dict[str, Any]) -> tuple[LiveOverlayMode, str | None]:
    """Return the overlay mode the live stream *live_defaults* name, and the decoder they name for it, if any."""
    try:
        mode = LiveOverlayMode.from_value(str(live_defaults.get("overlay_source", "none")))
    except ValueError:
        return LiveOverlayMode.NONE, None
    section = live_defaults.get(_HANDLER_SECTIONS.get(mode, ""), {}) or {}
    handler = section.get("data_stream_handler")
    return mode, None if handler is None else str(handler)


class TransportPages(QStackedWidget):
    """One page per live overlay mode, in ``LiveOverlayMode`` order, sized for the largest page.

    The None page explains every mode, and each other page starts with its own description. The MQTT page holds the
    broker fields and the data source; the WebSocket page, the DataHub topic and channel.
    """

    def __init__(
        self,
        raw_mqtt_defaults: dict[str, Any],
        mqtt_defaults: dict[str, Any],
        websocket_defaults: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
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

        self.mqtt_host_edit = QLineEdit()
        apply_env_hint(
            self.mqtt_host_edit,
            str(raw_mqtt_defaults.get("broker_host", "")),
        )
        mqtt_layout.addRow("MQTT Host", self.mqtt_host_edit)

        self.mqtt_username_edit = QLineEdit()
        apply_env_hint(
            self.mqtt_username_edit,
            str(raw_mqtt_defaults.get("broker_username", "")),
        )
        mqtt_layout.addRow("MQTT Username", self.mqtt_username_edit)

        self.mqtt_password_edit = QLineEdit()
        apply_env_hint(
            self.mqtt_password_edit,
            str(raw_mqtt_defaults.get("broker_password", "")),
            mask=True,
        )
        self.mqtt_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        mqtt_layout.addRow("MQTT Password", self.mqtt_password_edit)

        self.mqtt_port_edit = QLineEdit(str(integer_default(mqtt_defaults.get("broker_port"), 1883)))
        mqtt_layout.addRow("MQTT Port", self.mqtt_port_edit)

        self.data_source_choice = AnalyticsChoice(
            AnalyticsChoiceLoader(list_analytics_data_source_keys, "data sources", self),
            str(mqtt_defaults.get("data_source_key", "")),
        )
        mqtt_layout.addRow("Data Source", self.data_source_choice)

        websocket_layout = transport_forms[LiveOverlayMode.WEBSOCKET]

        self.websocket_topic_choice = AnalyticsChoice(
            AnalyticsChoiceLoader(list_datahub_topics, "DataHub topics", self),
            str(websocket_defaults.get("topic", "")),
        )
        websocket_layout.addRow("DataHub Topic", self.websocket_topic_choice)

        self.websocket_channel_id_edit = QLineEdit(str(integer_default(websocket_defaults.get("channel_id"), 1)))
        self.websocket_channel_id_edit.setToolTip("Instance of the DataHub topic to subscribe to. Most devices use 1.")
        websocket_layout.addRow("Topic Channel", self.websocket_channel_id_edit)

        self.choices = {
            LiveOverlayMode.MQTT: self.data_source_choice,
            LiveOverlayMode.WEBSOCKET: self.websocket_topic_choice,
        }
        """The analytics choice each mode discovers from the device."""
        for mode in LiveOverlayMode:
            self.addWidget(transport_pages[mode])
        self.forms = list(transport_forms.values())
        """The form of each page, in page order, so the dialog can align their label columns."""

    def mqtt_port(self) -> int:
        """Return the entered broker port without clamping inactive saved settings."""
        return _integer(self.mqtt_port_edit, "MQTT Port")

    def websocket_channel_id(self) -> int:
        """Return the entered channel id without an artificial upper bound."""
        return _integer(self.websocket_channel_id_edit, "Topic Channel")


def _integer(edit: QLineEdit, label: str) -> int:
    try:
        return int(edit.text())
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer.") from exc
