"""Where an Axis device's web API answers: protocol, host and port."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ax_devil_device_api import DeviceConfig
from ax_devil_device_api.core.config import Protocol


class DeviceProtocol(str, Enum):
    """The protocol a device's web API is reached with."""

    HTTPS = "https"
    HTTP = "http"

    @property
    def label(self) -> str:
        """Return the name shown to the user."""
        return self.name

    @property
    def default_port(self) -> int:
        """Return the port the protocol uses when none is given."""
        return 443 if self is DeviceProtocol.HTTPS else 80

    @property
    def websocket_scheme(self) -> str:
        """Return the WebSocket scheme that goes with this protocol."""
        return "wss" if self is DeviceProtocol.HTTPS else "ws"


class _ExactPortDeviceConfig(DeviceConfig):
    """A device configuration whose base URL omits only the port the protocol itself defaults to.

    The library omits ports 80 and 443 whatever the protocol, so HTTPS on port 80 would be asked on port 443.
    """

    def get_base_url(self) -> str:
        """Return the base URL, with the port unless it is the standard port of the protocol."""
        port = "" if self.port == self.protocol.default_port else f":{self.port}"
        return f"{self.protocol.value}://{self.host}{port}"


@dataclass(frozen=True)
class DeviceEndpoint:
    """Where a device's web API answers; port 0 means the protocol's standard port."""

    host: str
    protocol: DeviceProtocol
    port: int = 0

    def url(self, path: str, *, websocket: bool = False) -> str:
        """Return the URL of *path* (starting with ``/``) on this device, omitting a standard port."""
        scheme = self.protocol.websocket_scheme if websocket else self.protocol.value
        port = f":{self.port}" if self.port else ""
        return f"{scheme}://{self.host}{port}{path}"

    def device_config(self, username: str, password: str, *, timeout_s: float) -> DeviceConfig:
        """Return the device API configuration for this endpoint."""
        return _ExactPortDeviceConfig(
            host=self.host,
            username=username,
            password=password,
            protocol=Protocol(self.protocol.value),
            port=self.port or None,
            timeout=timeout_s,
            allow_insecure=self.protocol is DeviceProtocol.HTTP,
        )
