"""How to reach an Axis device's web API: VAPIX over HTTP or HTTPS, and the WebSocket upgrade on the same origin."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ax_devil_device_api import DeviceConfig


class DeviceProtocol(str, Enum):
    """The protocol a device's web API is reached with, in the order to try them."""

    HTTPS = "https"
    HTTP = "http"

    @property
    def websocket_scheme(self) -> str:
        """Return the WebSocket scheme that goes with this protocol."""
        return "wss" if self is DeviceProtocol.HTTPS else "ws"


@dataclass(frozen=True)
class DeviceConnection:
    """The device web API to talk to and how to sign in; port 0 means the protocol's standard port.

    The device API library omits ports 80 and 443 from its URLs whatever the protocol, so HTTPS on port 80 is
    asked on port 443 there; `url` does not share that limitation.
    """

    host: str
    protocol: DeviceProtocol
    port: int = 0
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    timeout_s: float = 10.0

    def url(self, path: str, *, websocket: bool = False) -> str:
        """Return the URL of *path* (starting with ``/``) on this device."""
        scheme = self.protocol.websocket_scheme if websocket else self.protocol.value
        port = f":{self.port}" if self.port else ""
        return f"{scheme}://{self.host}{port}{path}"

    def device_config(self) -> DeviceConfig:
        """Return the device API library's configuration for this connection."""
        from ax_devil_device_api import DeviceConfig  # Imported here so importing core stays light.
        from ax_devil_device_api.core.config import Protocol

        return DeviceConfig(
            host=self.host,
            username=self.username,
            password=self.password,
            protocol=Protocol(self.protocol.value),
            port=self.port or None,
            timeout=self.timeout_s,
            allow_insecure=self.protocol is DeviceProtocol.HTTP,
        )
