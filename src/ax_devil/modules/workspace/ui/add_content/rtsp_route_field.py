"""The live stream dialog's RTSP row: reach the RTSP port directly, or tunnel RTSP through the device's web API."""

from __future__ import annotations

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from ax_devil.core.device_connection import RtspRoute
from ax_devil.modules.chrome.choice_toggle import ChoiceToggle
from ax_devil.modules.chrome.tokens import Space


class RtspRouteField(QWidget):
    """A route toggle beside the RTSP port, which only the direct route uses; tunnels use the web API's port."""

    def __init__(self, route: RtspRoute, port_default: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._port_default = port_default
        self.toggle = ChoiceToggle([(choice.value, choice.label) for choice in RtspRoute], route.value, self)
        self.port_edit = QLineEdit(self)
        self.port_edit.setPlaceholderText(str(port_default))
        self.port_edit.setValidator(QIntValidator(1, 65535, self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        layout.addWidget(self.toggle)
        layout.addWidget(self.port_edit, 1)
        self.toggle.changed.connect(self._show_route)
        self._show_route(self.toggle.value())

    def route(self) -> RtspRoute:
        """Return the chosen route."""
        return RtspRoute(self.toggle.value())

    def port(self) -> int:
        """Return the entered RTSP port, or the default when it is empty."""
        return int(self.port_edit.text() or 0) or self._port_default

    def _show_route(self, value: str) -> None:
        self.port_edit.setEnabled(RtspRoute(value) is RtspRoute.DIRECT)
