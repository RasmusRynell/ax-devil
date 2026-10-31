"""Reading a camera's resolutions from its ``param.cgi`` answer.

The bodies are built from the VAPIX documentation and Kane610 (``axis``) fixtures, not captured from a camera.
"""

from __future__ import annotations

from typing import Any

import pytest
from ax_devil_device_api.utils.errors import AuthenticationError
from requests import PreparedRequest, Response
from requests.adapters import HTTPAdapter

from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.data_sources.live.camera_resolutions import parse_camera_resolutions, read_camera_resolutions

FIRMWARE_11_7 = (
    "root.Properties.Image.I0.H264.Resolution=1920x1080,1280x720,640x360\r\n"
    "root.Properties.Image.I1.H264.Resolution=1280x720,640x480\r\n"
    "root.Properties.Image.I0.Resolution=320x240\r\n"
)
PER_CHANNEL = "root.Properties.Image.I0.Resolution=1920x1080,1280x720\r\nroot.Properties.Image.Resolution=640x480\r\n"
GLOBAL_ONLY = "root.Properties.Image.Resolution=1920x1080,D1,1280x720,CIF,1920x1080\r\n"


@pytest.mark.parametrize(
    ("body", "head", "listed"),
    [
        (FIRMWARE_11_7, 1, ("1920x1080", "1280x720", "640x360")),
        (FIRMWARE_11_7, 2, ("1280x720", "640x480")),
        (PER_CHANNEL, 1, ("1920x1080", "1280x720")),
        (GLOBAL_ONLY, 3, ("1920x1080", "1280x720")),
        ("# Error: Error -1 getting param in group 'Properties.Image'\r\n", 1, ()),
        ("", 1, ()),
    ],
)
def test_a_channel_offers_the_resolutions_its_camera_lists(body: str, head: int, listed: tuple[str, ...]) -> None:
    """Discovery lists valid sizes for the channel in camera order without duplicates."""
    assert parse_camera_resolutions(body).channel(head) == listed


@pytest.mark.parametrize("protocol", [DeviceProtocol.HTTPS, DeviceProtocol.HTTP])
def test_basic_only_camera_requires_encrypted_connection(
    monkeypatch: pytest.MonkeyPatch, protocol: DeviceProtocol
) -> None:
    """Basic-only cameras list resolutions over HTTPS without exposing credentials over HTTP."""
    authorizations: list[str] = []

    def send(adapter: HTTPAdapter, request: PreparedRequest, **kwargs: Any) -> Response:
        authorization = str(request.headers.get("Authorization", ""))
        authorizations.append(authorization)
        response = Response()
        response.request = request
        response.connection = adapter
        response.status_code = 200 if authorization.startswith("Basic ") else 401
        response.headers["WWW-Authenticate"] = 'Basic realm="camera"'
        response._content = GLOBAL_ONLY.encode()
        return response

    monkeypatch.setattr(HTTPAdapter, "send", send)
    connection = DeviceConnection("camera.invalid", protocol, username="test-user", password="test-password")

    if protocol is DeviceProtocol.HTTPS:
        assert read_camera_resolutions(connection).channel(1) == ("1920x1080", "1280x720")
    else:
        with pytest.raises(AuthenticationError):
            read_camera_resolutions(connection)
        assert authorizations
        assert all(not authorization.startswith("Basic ") for authorization in authorizations)
