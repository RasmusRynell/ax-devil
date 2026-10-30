"""Reading a camera's resolutions from its ``param.cgi`` answer.

The bodies are built from the VAPIX documentation and Kane610 (``axis``) fixtures, not captured from a camera.
"""

from __future__ import annotations

import pytest

from ax_devil.modules.data_sources.live.camera_resolutions import parse_camera_resolutions

FIRMWARE_11_7 = (
    "root.Properties.Image.I0.H264.Resolution=1920x1080,1280x720,640x360\r\n"
    "root.Properties.Image.I1.H264.Resolution=1280x720,640x480\r\n"
    "root.Properties.Image.I0.Resolution=320x240\r\n"
    "root.Image.I0.Appearance.Resolution=1280x720\r\n"
    "root.Image.I1.Appearance.Resolution=640x480\r\n"
)
PER_CHANNEL = "root.Properties.Image.I0.Resolution=1920x1080,1280x720\r\nroot.Properties.Image.Resolution=640x480\r\n"
GLOBAL_ONLY = "root.Properties.Image.Resolution=1920x1080,D1,1280x720,CIF,1920x1080\r\n"
DEFAULT_NOT_OFFERED = (
    "root.Properties.Image.I0.H264.Resolution=1920x1080,1280x720\r\nroot.Image.I0.Appearance.Resolution=800x600\r\n"
)


@pytest.mark.parametrize(
    ("body", "head", "configured", "listed", "default", "preferred"),
    [
        (FIRMWARE_11_7, 1, "640x360", ("1920x1080", "1280x720", "640x360"), "1280x720", "640x360"),
        (FIRMWARE_11_7, 2, "640x360", ("1280x720", "640x480"), "640x480", "640x480"),
        (PER_CHANNEL, 1, "1280x720", ("1920x1080", "1280x720"), "", "1280x720"),
        (GLOBAL_ONLY, 3, "800x600", ("1920x1080", "1280x720"), "", "1920x1080"),
        ("# Error: Error -1 getting param in group 'Image'\r\n", 1, "1280x720", (), "", "1280x720"),
        ("", 1, "1280x720", (), "", "1280x720"),
        (DEFAULT_NOT_OFFERED, 1, "800x600", ("1920x1080", "1280x720"), "", "1920x1080"),
        (DEFAULT_NOT_OFFERED, 1, "1280x720", ("1920x1080", "1280x720"), "", "1280x720"),
    ],
)
def test_a_channel_offers_the_resolutions_its_camera_lists(
    body: str, head: int, configured: str, listed: tuple[str, ...], default: str, preferred: str
) -> None:
    """A stream starts on a resolution the camera offers: the configured one, else the camera's own default."""
    channel = parse_camera_resolutions(body).channel(head)

    assert (channel.resolutions, channel.default, channel.preferred(configured)) == (listed, default, preferred)
