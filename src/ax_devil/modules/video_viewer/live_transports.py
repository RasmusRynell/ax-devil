"""Opening a live stream's transports: the RTSP address and the embedded or separate overlay source."""

from __future__ import annotations

from ax_devil.modules.data_sources.base import OverlaySource
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder, rtsp_stream_config
from ax_devil.modules.plugin_system import get_payload_decoder, get_payload_filter_factory
from ax_devil.modules.workspace.core import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
)


def open_overlay_transport(content: LiveVideoContent) -> tuple[RTSPOverlayDecoder | None, OverlaySource | None]:
    """Return the decoder for overlays embedded in RTSP, or the separate overlay transport, for *content*."""
    from ax_devil.modules.data_sources.live.mqtt_overlay_source import MQTTOverlaySource
    from ax_devil.modules.data_sources.live.websocket_overlay_source import WebSocketOverlaySource

    source_spec = content.overlay_spec
    if source_spec is None:
        return None, None
    decoder = get_payload_decoder(source_spec.handler_type)
    filter_factory = get_payload_filter_factory(source_spec.handler_type)
    match source_spec:
        case LiveRTSPOverlaySourceSpec():
            embedded = RTSPOverlayDecoder(
                decoder=decoder, handler_type=source_spec.handler_type, filter_factory=filter_factory
            )
            return embedded, None
        case LiveMQTTOverlaySourceSpec():
            return None, MQTTOverlaySource(
                broker_host=source_spec.broker_host,
                broker_port=source_spec.broker_port,
                broker_username=source_spec.broker_username,
                broker_password=source_spec.broker_password,
                device_host=content.source_spec.host,
                device_username=content.source_spec.username,
                device_password=content.source_spec.password,
                device_api_protocol=source_spec.device_api_protocol,
                analytics_data_source_key=source_spec.analytics_data_source_key,
                decoder=decoder,
                handler_type=source_spec.handler_type,
                filter_factory=filter_factory,
            )
        case LiveWebSocketOverlaySourceSpec():
            return None, WebSocketOverlaySource(
                topic=source_spec.topic,
                channel_id=source_spec.channel_id,
                device_host=content.source_spec.host,
                device_username=content.source_spec.username,
                device_password=content.source_spec.password,
                device_api_protocol=source_spec.device_api_protocol,
                decoder=decoder,
                handler_type=source_spec.handler_type,
                filter_factory=filter_factory,
            )


def build_rtsp_url(source_spec: LiveRTSPStreamSpec, *, metadata: bool) -> str:
    """Return the stream URL of *source_spec*, built for the device unless the spec names one."""
    from ax_devil_rtsp import build_axis_rtsp_url

    if source_spec.stream_url:
        return source_spec.stream_url
    return str(
        build_axis_rtsp_url(
            source_spec.host,
            rtsp_stream_config(metadata=metadata),
            username=source_spec.username,
            password=source_spec.password,
            camera=source_spec.camera_head,
            resolution=source_spec.resolution,
        )
    )
