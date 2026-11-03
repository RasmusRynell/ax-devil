"""Live stream URLs preserve explicit resolution choices and otherwise defer to the camera."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from ax_devil.modules.video_viewer.live_transports import build_rtsp_url
from ax_devil.modules.workspace.core.source_specs import LiveRTSPStreamSpec


@pytest.mark.parametrize("resolution", ["", "640x480"])
def test_rtsp_resolution_is_only_requested_when_explicit(resolution: str) -> None:
    """Camera default omits the RTSP parameter; an explicit choice is sent unchanged."""
    spec = LiveRTSPStreamSpec(host="camera.invalid", username="", password="", resolution=resolution)

    url = build_rtsp_url(spec, metadata=False, timeout_s=10.0)

    assert parse_qs(urlsplit(url).query).get("resolution", []) == ([resolution] if resolution else [])
