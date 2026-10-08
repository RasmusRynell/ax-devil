"""Controller for live stream playback with overlay support."""

from __future__ import annotations

import time
import uuid
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from ax_devil.modules.scene.rendering.catalog import SceneRenderCatalog
    from ax_devil.modules.scene.rendering.catalog_manager import SceneRenderCatalogSelection
    from ax_devil.modules.video_viewer.media_tools import EntityFilterWidget
    from ax_devil.modules.video_viewer.scene_inspection import SceneInspectorSink


from PySide6.QtCore import QTimer, SignalInstance

from ax_devil.core.data_types import FrameData, OverlayData
from ax_devil.modules.data_sources.base import DataSource, FrameSource, OverlaySource
from ax_devil.modules.data_sources.live.rtsp_source import RTSPOverlayDecoder, RTSPSource, rtsp_stream_config
from ax_devil.modules.diagnostics.metrics_store import (
    get_metrics_store,
    metrics_enabled,
    remove_instance,
    set_metric,
    source_identity,
)
from ax_devil.modules.filtering import FilterConfig
from ax_devil.modules.plugin_system import get_payload_decoder, get_payload_filter_factory
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.synchronization import QtStreamSync, SyncResult
from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.workspace import (
    LiveMQTTOverlaySourceSpec,
    LiveRTSPOverlaySourceSpec,
    LiveRTSPStreamSpec,
    LiveVideoContent,
    LiveWebSocketOverlaySourceSpec,
)

from .live_connection import LiveConnectionStatus, LiveFeed, LiveFeedStatus
from .overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings
from .scene_frame_presenter import SceneFramePresenter, describe_frame_id, describe_overlay_data
from .scene_inspection import schedule_scene_inspection_update

logger = get_logger(__name__)

VIDEO_STALL_TIMEOUT_S = 5.0
"""Seconds without video frames after which a live video feed counts as stalled."""


class StreamMediaController:
    """Controller for live streaming with synchronized overlays."""

    def __init__(
        self,
        frame_display: FrameDisplay,
        content: LiveVideoContent,
        *,
        render_catalog_selection: SceneRenderCatalogSelection,
        frame_displayed: Callable[[VideoFrameWithOverlays], None] | None = None,
        connection_changed: Callable[[LiveConnectionStatus], None] | None = None,
    ) -> None:
        self._logger = logger

        self.frame_display: Optional[FrameDisplay] = frame_display
        self._frame_displayed = frame_displayed
        self._content = content
        self._render_catalog_selection = render_catalog_selection
        self._filter_widget: Optional["EntityFilterWidget"] = None
        self._scene_inspector: Optional["SceneInspectorSink"] = None
        self._presenter = SceneFramePresenter(scene_render_catalog=self._render_catalog_selection.active_catalog())
        self._overlay_persistence = OverlayPersistenceSettings.default_enabled()
        self._overlay_policy = OverlayPersistencePolicy(self._overlay_persistence.copy())
        self._metrics_id = source_identity(self)
        self._paused: bool = False
        self._connection_changed = connection_changed
        self._source_slots: list[tuple[SignalInstance, Callable[..., None]]] = []
        self._last_video_data_s = time.monotonic()
        self._stall_timer = QTimer()
        self._stall_timer.setInterval(1000)
        self._stall_timer.timeout.connect(self._check_video_stall)

        self.synchronizer: Optional[QtStreamSync] = self._init_synchronizer(frame_display)
        self.video_source: FrameSource | None = None
        self.overlay_source: OverlaySource | None = None
        self._sources: list[DataSource] = []
        self._open_sources()
        self._connection = LiveConnectionStatus.connecting(self._feeds())

        self._render_catalog_selection.activeCatalogChanged.connect(self.attach_scene_render_catalog)
        get_metrics_store().register(self._metrics_id, f"Live stream · {content.display_name}")
        get_metrics_store().register(self._sync_metrics_id, f"Synchronization · {content.display_name}")
        self._connect_sources()

    def _open_sources(self) -> None:
        """Create the video transport and any separate overlay transport for the content."""
        content = self._content
        overlay_spec = content.overlays[0].source_spec if content.overlays else None
        embedded_overlay = None
        if isinstance(overlay_spec, LiveRTSPOverlaySourceSpec):
            embedded_overlay = RTSPOverlayDecoder(
                decoder=get_payload_decoder(overlay_spec.handler_type),
                handler_type=overlay_spec.handler_type,
                filter_factory=get_payload_filter_factory(overlay_spec.handler_type),
            )
        rtsp_source = RTSPSource(
            self._build_rtsp_url(content.source_spec, metadata=embedded_overlay is not None),
            overlay=embedded_overlay,
        )
        overlay_source: OverlaySource | None = None
        try:
            if embedded_overlay is not None:
                overlay_source = rtsp_source
            elif isinstance(overlay_spec, (LiveMQTTOverlaySourceSpec, LiveWebSocketOverlaySourceSpec)):
                overlay_source = self._create_overlay_source(content, overlay_spec)
        except Exception:
            rtsp_source.stop()
            rtsp_source.deleteLater()
            raise
        self.video_source = rtsp_source
        self.overlay_source = overlay_source
        self._sources = list(dict.fromkeys((rtsp_source, overlay_source or rtsp_source)))

    def _feeds(self) -> list[LiveFeed]:
        """Return the feed carried by each owned transport, video first."""
        return [LiveFeed.VIDEO if source is self.video_source else LiveFeed.OVERLAY for source in self._sources]

    @property
    def connection_status(self) -> LiveConnectionStatus:
        """Return the connection status of every live feed."""
        return self._connection

    def _update_feed(self, feed: LiveFeed, change: Callable[[LiveFeedStatus], LiveFeedStatus]) -> None:
        current = self._connection.feed(feed)
        updated = change(current)
        if updated == current:
            return
        self._logger.debug(f"Live {feed.label.lower()} feed: {updated.summary}")
        self._publish_connection(self._connection.with_status(updated))

    def _publish_connection(self, status: LiveConnectionStatus) -> None:
        self._connection = status
        if self._connection_changed is not None:
            self._connection_changed(status)

    def _mark_live(self, feed: LiveFeed) -> None:
        """Mark a feed live; video connection or frames also restart the stall clock."""
        if feed is LiveFeed.VIDEO:
            self._last_video_data_s = time.monotonic()
        self._update_feed(feed, LiveFeedStatus.live)

    def _on_source_reconnecting(self, feed: LiveFeed, message: str) -> None:
        self._logger.warning(f"Live {feed.label.lower()} source reconnecting: {message}")
        self._update_feed(feed, lambda status: status.reconnecting(message))

    def _on_source_error(self, feed: LiveFeed, message: str) -> None:
        self._logger.error(f"Stream source error: {message}")
        self._update_feed(feed, lambda status: status.failed(message))

    def _check_video_stall(self) -> None:
        if self._paused or not self._sources:
            return
        silent_s = time.monotonic() - self._last_video_data_s
        if silent_s < VIDEO_STALL_TIMEOUT_S:
            return
        self._update_feed(LiveFeed.VIDEO, lambda status: status.stalled(silent_s))

    def _add_frame(self, frame_data: FrameData) -> None:
        if self._paused:
            return
        assert self.synchronizer is not None
        self._mark_live(LiveFeed.VIDEO)
        capture_time_s = frame_data.frame_id.timestamp_monotime_us / 1000000.0
        self._record_metric("Last decoded frame", lambda: describe_frame_id(frame_data.frame_id))
        self.synchronizer.push_frame(frame_data, capture_time_s)

    def _add_overlay(self, overlay_data: OverlayData) -> None:
        if self._paused:
            return
        assert self.synchronizer is not None
        capture_time_s = overlay_data.frame_id.timestamp_monotime_us / 1000000.0
        self._record_metric("Last decoded overlay", lambda: describe_overlay_data(overlay_data))
        self.synchronizer.push_overlay(overlay_data, capture_time_s)

    def _on_sync_result(self, result: SyncResult[Any, Any]) -> None:
        if self.frame_display is None:
            return

        frame_id = result.frame.data.frame_id
        candidate_overlay = result.overlay.data if result.overlay else None
        self._record_metric("Last synced frame", lambda: describe_frame_id(frame_id))
        self._record_metric("Last synced overlay", lambda: describe_overlay_data(candidate_overlay))
        presentation = self._presenter.prepare_frame(
            result.frame.data, candidate_overlay, overlay_policy=self._overlay_policy
        )

        if presentation.overlay_reused:
            overlay_opacity = presentation.metadata.get("overlay_opacity", 1.0)
            self._logger.debug(f"Reusing cached overlay (opacity={overlay_opacity:.2f})")
        display_frame = presentation.display_frame
        self.frame_display.display_frame(display_frame)
        if self._frame_displayed is not None:
            self._frame_displayed(display_frame)
        schedule_scene_inspection_update(self._scene_inspector, presentation.inspection)

    def _on_filter_changed(self) -> None:
        if self.frame_display is None:
            return
        self.frame_display.refresh_overlays()

    def start_playback(self) -> None:
        """Start every live transport and watch the video feed for stalls."""
        self._logger.debug("Starting live stream playback")
        self._overlay_policy.reset()
        self._last_video_data_s = time.monotonic()
        self._stall_timer.start()
        for source in self._sources:
            source.play()

    def retry(self) -> None:
        """Reopen every live transport and start playing again."""
        if self.frame_display is None:
            return
        self._logger.info(f"Retrying live stream {self._content.display_name}")
        self._release_sources()
        if self.synchronizer is not None:
            self.synchronizer.reset()
        self._paused = False
        try:
            self._open_sources()
        except Exception as exc:
            reason = f"Failed to reopen live stream: {exc}"
            self._logger.error(reason)
            self._publish_connection(LiveConnectionStatus((LiveFeedStatus(LiveFeed.VIDEO).failed(reason),)))
            return
        self._publish_connection(LiveConnectionStatus.connecting(self._feeds()))
        self._connect_sources()
        self.start_playback()

    @property
    def is_paused(self) -> bool:
        """Whether playback is currently paused."""
        return self._paused

    def pause_playback(self) -> None:
        """Pause live stream sources. Connections stay alive."""
        self._logger.debug("Pausing live stream playback")
        self._paused = True
        for source in self._sources:
            source.pause()

    def resume_playback(self) -> None:
        """Resume live stream. Flushes stale sync buffers first."""
        self._logger.debug("Resuming live stream playback")
        if self.synchronizer is not None:
            self.synchronizer.reset()
        self._paused = False
        self._last_video_data_s = time.monotonic()
        for source in self._sources:
            source.play()

    def cleanup(self) -> None:
        """Release signal connections and each owned transport exactly once."""
        if self.frame_display is None:
            return
        self._logger.debug("Shutting down stream media controller")

        try:
            self._render_catalog_selection.activeCatalogChanged.disconnect(self.attach_scene_render_catalog)
        except Exception:
            self._logger.debug("Render catalog manager disconnect failed or already disconnected")
        self._stall_timer.stop()
        self._disconnect_synchronizer_signals()
        self._release_sources()
        self._cleanup_synchronizer()
        self._clear_references()
        self._clear_metrics()

    def get_filter_config(self) -> FilterConfig | None:
        return self.overlay_source.get_filter_config() if self.overlay_source else None

    def attach_filter_widget(self, filter_widget: "EntityFilterWidget") -> None:
        if self._filter_widget is filter_widget:
            return

        if self._filter_widget is not None:
            try:
                self._filter_widget.filterChanged.disconnect(self._on_filter_changed)
            except Exception:
                self._logger.debug("filterChanged disconnect failed or already disconnected")

        self._filter_widget = filter_widget
        self._presenter.attach_filter_widget(filter_widget)
        self._filter_widget.filterChanged.connect(self._on_filter_changed)

    def attach_scene_inspector(self, scene_inspector: "SceneInspectorSink | None") -> None:
        """Register a scene inspector sink for entity list updates."""
        self._scene_inspector = scene_inspector
        if scene_inspector is None:
            return
        scene_inspector.clear()

    def attach_scene_render_catalog(self, scene_render_catalog: "SceneRenderCatalog") -> None:
        """Set the Scene Render Catalog used for future overlay presentation."""
        self._presenter.attach_scene_render_catalog(scene_render_catalog)
        self._on_filter_changed()

    def set_overlay_persistence(self, persistence_settings: OverlayPersistenceSettings) -> None:
        """Update sticky overlay configuration at runtime."""
        snapshot = persistence_settings.copy()
        self._overlay_persistence = snapshot
        self._overlay_policy.update_settings(snapshot)

    def _init_synchronizer(self, frame_display: FrameDisplay) -> QtStreamSync:
        delay_ms = 1500

        sync_id = f"sync:{uuid.uuid4()}"
        self._sync_metrics_id = sync_id
        frame_display.viewport.set_diagnostics_sources((self._metrics_id, sync_id))
        synchronizer = QtStreamSync(
            object_name=sync_id,
            parent=frame_display,
            delay_ms=delay_ms,
        )
        synchronizer.syncReady.connect(self._on_sync_result)
        return synchronizer

    def _create_overlay_source(
        self,
        content: LiveVideoContent,
        source_spec: LiveMQTTOverlaySourceSpec | LiveWebSocketOverlaySourceSpec,
    ) -> OverlaySource:
        from ax_devil.modules.data_sources import MQTTOverlaySource, WebSocketOverlaySource

        decoder = get_payload_decoder(source_spec.handler_type)
        filter_factory = get_payload_filter_factory(source_spec.handler_type)
        if isinstance(source_spec, LiveMQTTOverlaySourceSpec):
            return MQTTOverlaySource(
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
        return WebSocketOverlaySource(
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

    def _build_rtsp_url(self, source_spec: LiveRTSPStreamSpec, *, metadata: bool) -> str:
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

    def _connect_sources(self) -> None:
        assert self.video_source is not None
        self._connect_source_signal(self.video_source, self.video_source.frameReady, self._add_frame)
        if self.overlay_source is not None:
            self._connect_source_signal(self.overlay_source, self.overlay_source.overlayReady, self._add_overlay)
        for source, feed in zip(self._sources, self._feeds()):
            for signal, slot in (
                (source.sourceConnected, partial(self._mark_live, feed)),
                (source.sourceReconnecting, partial(self._on_source_reconnecting, feed)),
                (source.sourceError, partial(self._on_source_error, feed)),
            ):
                self._connect_source_signal(source, signal, slot)

    def _connect_source_signal(self, source: DataSource, signal: SignalInstance, slot: Callable[..., None]) -> None:
        callback = partial(self._dispatch_source_event, source, slot)
        self._source_slots.append((signal, callback))
        signal.connect(callback)

    def _dispatch_source_event(self, source: DataSource, slot: Callable[..., None], *args: Any) -> None:
        # Disconnecting a Qt signal does not discard callbacks already queued on the GUI thread.
        if source in self._sources:
            slot(*args)

    def _disconnect_synchronizer_signals(self) -> None:
        if self.synchronizer is not None:
            try:
                self.synchronizer.syncReady.disconnect(self._on_sync_result)
            except Exception:
                self._logger.debug("syncReady disconnect failed or already disconnected")
        if self._filter_widget is not None:
            try:
                self._filter_widget.filterChanged.disconnect(self._on_filter_changed)
            except Exception:
                self._logger.debug("filterChanged disconnect failed or already disconnected")

    def _disconnect_source_signals(self) -> None:
        for signal, slot in self._source_slots:
            try:
                signal.disconnect(slot)
            except Exception:
                self._logger.debug("Source signal disconnect failed or already disconnected")
        self._source_slots = []

    def _release_sources(self) -> None:
        """Disconnect, stop and delete every owned transport."""
        self._disconnect_source_signals()
        self._stop_and_wait_sources()
        self._delete_source_objects()
        self.video_source = None
        self.overlay_source = None
        self._sources = []

    def _stop_and_wait_sources(self) -> None:
        for source in self._sources:
            source.stop()

    def _delete_source_objects(self) -> None:
        for source in self._sources:
            try:
                source.deleteLater()
            except Exception:
                pass

    def _cleanup_synchronizer(self) -> None:
        if self.synchronizer is not None:
            try:
                self.synchronizer.cleanup()
            except Exception:
                self._logger.debug("synchronizer cleanup failed or already cleaned")

    def _clear_references(self) -> None:
        self.synchronizer = None
        self.frame_display = None
        self._frame_displayed = None
        self._connection_changed = None
        self._filter_widget = None
        self._scene_inspector = None
        self._presenter = SceneFramePresenter()

    def _clear_metrics(self) -> None:
        remove_instance(self._metrics_id)

    def _record_metric(self, key: str, value: str | Callable[[], str]) -> None:
        if not metrics_enabled():
            return
        resolved = value() if callable(value) else value
        set_metric(self._metrics_id, key, resolved)
