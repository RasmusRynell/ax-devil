"""The stream resolutions a camera offers per video channel, read from its VAPIX parameters.

The lists come from ``Properties.Image``; see
https://developer.axis.com/vapix/network-video/parameter-management/image-api/ and
https://developer.axis.com/video-streaming-and-recording/video-streaming/how-to-guides/lookup-channels/ .
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ax_devil_device_api import Client
from ax_devil_device_api.core.config import AuthMethod
from ax_devil_device_api.features.device_info import DeviceInfoClient

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_RESOLUTION = re.compile(r"[1-9]\d*x[1-9]\d*")


@dataclass(frozen=True)
class CameraResolutions:
    """A camera's ``param.cgi`` values, keyed without the leading ``root.``."""

    params: Mapping[str, str]

    def channel(self, camera_head: int) -> tuple[str, ...]:
        """Return what the video channel of *camera_head* (counted from 1) offers."""
        n = camera_head - 1
        ladder = (
            f"Properties.Image.I{n}.H264.Resolution",
            f"Properties.Image.I{n}.Resolution",
            "Properties.Image.Resolution",
        )
        listed = next((self.params[key] for key in ladder if self.params.get(key)), "")
        return tuple(
            dict.fromkeys(token for token in map(str.strip, listed.split(",")) if _RESOLUTION.fullmatch(token))
        )


def parse_camera_resolutions(text: str) -> CameraResolutions:
    """Return the ``key=value`` lines of a ``param.cgi`` answer; an error body or an empty one gives no values."""
    params: dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.strip("\r")
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.removeprefix("root.")] = value
    return CameraResolutions(params)


def read_camera_resolutions(connection: DeviceConnection) -> CameraResolutions:
    """Ask the camera at *connection* for its image parameters in one request; raises when it does not answer 200.

    HTTPS allows automatic authentication. HTTP forces Digest so Basic cannot expose credentials in clear text.
    """
    config = connection.device_config()
    config.auth_method = AuthMethod.AUTO if connection.protocol is DeviceProtocol.HTTPS else AuthMethod.DIGEST
    with Client(config) as client:
        response = client.device.request(
            DeviceInfoClient.PARAMS_ENDPOINT,
            params={"action": "list", "group": "Properties.Image"},
            headers={"Accept": "text/plain"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"{connection.host} answered HTTP {response.status_code} listing image parameters")
    logger.debug(f"Read image parameters from {connection.host}")
    return parse_camera_resolutions(response.text)
