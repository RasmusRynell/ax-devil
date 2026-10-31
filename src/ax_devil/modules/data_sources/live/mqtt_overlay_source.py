"""MQTT-based overlay source implementation.

Real-time overlay streaming via MQTT with device connection support. Uses Worker pattern for background threading and
lifecycle management.
"""

from threading import Lock
from typing import Any

from ax_devil_device_api import DeviceConfig
from ax_devil_mqtt import AxisAnalyticsMqttClient, MqttMessage
from PySide6.QtCore import QObject

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.core.device_connection import DeviceConnection
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger, redact_dependency_logs

from ..base import OverlaySource, Worker
from .mqtt_diagnostics import MqttDiagnostics, UnsupportedAnalyticsDataSourceError, terminal_mqtt_failure
from .mqtt_discovery import list_mqtt_data_source_keys

logger = get_logger(__name__)
RETRY_INITIAL_DELAY_S = 0.25
RETRY_MAX_DELAY_S = 8.0


class MQTTWorker(Worker):
    """Worker thread for MQTT message processing."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        broker_username: str,
        broker_password: str,
        device: DeviceConnection,
        analytics_data_source_key: str,
        decoder: PayloadToSceneDecoder,
        source_id: str = "mqtt_overlay",
    ) -> None:
        super().__init__(source_id)

        self.broker_host = broker_host
        self.broker_port = broker_port
        self.broker_username = broker_username
        self.broker_password = broker_password
        self.device = device
        self.analytics_data_source_key = analytics_data_source_key
        self.decoder = decoder

        self.analytics_manager: AxisAnalyticsMqttClient | None = None
        self.device_config: DeviceConfig | None = None
        self.latest_message: MqttMessage | None = None
        self._message_lock = Lock()
        self.message_count = 0
        self._connected = False
        self._stop_token = CancelToken()
        self._retry_delay_s = RETRY_INITIAL_DELAY_S
        self._diagnostics = MqttDiagnostics((broker_password, device.password))

        # Signal to parent overlay source
        self.overlay_ready_signal: Any | None = None

    def set_overlay_ready_signal(self, signal: Any) -> None:
        """Set the signal to emit when overlay is ready."""
        self.overlay_ready_signal = signal

    def run(self) -> None:
        """Keep transport cleanup on the worker, after any pending setup returns."""
        # The dependency logs setup/cleanup exceptions before handing them back to us.
        with redact_dependency_logs(
            ("ax_devil_mqtt.core.manager", "ax_devil_mqtt.core.temporary_analytics_mqtt_publisher"),
            self._diagnostics.redact,
        ):
            try:
                super().run()
            finally:
                self._cleanup_connection()
                self._running = False

    def run_loop(self) -> bool:
        """Run one cancellable setup attempt or message-processing iteration."""
        try:
            self._stop_token.raise_if_cancelled()
            if not self._connected:
                try:
                    self._connect()
                except Exception as exc:
                    self._stop_token.raise_if_cancelled()
                    reason = self._diagnostics.redact(f"Connection failed: {exc}")
                    if terminal_mqtt_failure(exc):
                        logger.error(reason)
                        self.sourceError.emit(reason)
                        return False
                    logger.warning(reason)
                    self.sourceReconnecting.emit(reason)
                    self._stop_token.wait(self._retry_delay_s)
                    self._retry_delay_s = min(self._retry_delay_s * 2, RETRY_MAX_DELAY_S)
                    return True
            self._process_message()
            self._stop_token.wait(0.01)
            return True
        except Cancelled:
            return False

    def _process_message(self) -> None:
        overlay_ready_signal = self.overlay_ready_signal
        with self._message_lock:
            message = self.latest_message if overlay_ready_signal else None
            if message is not None:
                self.latest_message = None
        if message is not None and overlay_ready_signal is not None:
            try:
                self.message_count += 1
                scene = self.decoder.decode(message.payload)
                if scene is None:
                    logger.warning(
                        f"No scene emitted for MQTT message: decoder returned None; "
                        f"source={self.source_id!r}, topic={message.topic!r}"
                    )
                    return

                if not scene.time_slice.is_instant:
                    logger.warning(
                        "Non-instant time slices are not supported for streamed data,\
                              things might not work correctly! be careful"
                    )

                capture_time_us = scene.time_slice.start_timestamp_us
                if capture_time_us is None:
                    raise ValueError("Streamed data with ints as timestamps is not supported at this moment")

                # Create FrameIdentifier using device-provided timestamp from MQTT
                frame_id = FrameIdentifier(sequence_id=self.message_count, timestamp_us=capture_time_us)

                overlay_data = OverlayData(
                    content=scene,
                    frame_id=frame_id,
                    source_id=self.source_id,
                    metadata={"mqtt_message": message},
                )

                overlay_ready_signal.emit(overlay_data)

            except Exception as e:
                logger.error(
                    f"Dropping MQTT message after processing failed: source={self.source_id!r}, "
                    f"topic={message.topic!r}, error={type(e).__name__}. Sparse decoder state may be incomplete.",
                    exc_info=True,
                )

    def _connect(self) -> None:
        """Establish connection to device and MQTT broker."""
        try:
            self._stop_token.raise_if_cancelled()
            self.device_config = self.device.device_config()
            self._validate_analytics_data_source()
            self._stop_token.raise_if_cancelled()

            self.analytics_manager = AxisAnalyticsMqttClient(
                broker_host=self.broker_host,
                broker_port=self.broker_port,
                device_config=self.device_config,
                analytics_data_source_key=self.analytics_data_source_key,
                message_callback=self._on_message_received,
                broker_username=self.broker_username,
                broker_password=self.broker_password,
            )
            self._stop_token.raise_if_cancelled()
            self.analytics_manager.start()  # Raise exception on failure
            self._stop_token.raise_if_cancelled()
            self._connected = True
            self._retry_delay_s = RETRY_INITIAL_DELAY_S
            logger.info("MQTT connection established")
            self.sourceConnected.emit()

        finally:
            if not self._connected:
                self._cleanup_connection()

    def _validate_analytics_data_source(self) -> None:
        """Reject unavailable data sources before publisher creation with an actionable error."""
        assert self.device_config is not None
        available_keys = list_mqtt_data_source_keys(self.device_config)
        if self.analytics_data_source_key in available_keys:
            return

        channel_suffix = self.analytics_data_source_key.rpartition("#")[2]
        same_channel_keys = tuple(
            key for key in available_keys if channel_suffix and key.endswith(f"#{channel_suffix}")
        )
        relevant_keys = same_channel_keys or available_keys
        displayed_keys = relevant_keys[:8]
        remaining_count = len(relevant_keys) - len(displayed_keys)
        available_text = ", ".join(displayed_keys) if displayed_keys else "none"
        if remaining_count > 0:
            available_text = f"{available_text} (+{remaining_count} more)"

        raise UnsupportedAnalyticsDataSourceError(
            f"Analytics data source '{self.analytics_data_source_key}' is not available on device "
            f"{self.device.host}. Available sources: {available_text}. Choose a source in Add Live Stream, or pass "
            "--data-source with a matching --handler-type."
        )

    def _on_message_received(self, message: MqttMessage) -> None:
        """Callback for new MQTT messages."""
        with self._message_lock:
            dropped = self.latest_message
            self.latest_message = message
        if dropped is not None:
            logger.warning(
                f"Dropping undecoded MQTT message: replaced by newer message; "
                f"source={self.source_id!r}, topic={dropped.topic!r}. Sparse decoder state may be incomplete."
            )

    def request_stop(self) -> None:
        """Request worker-thread termination without racing connection setup cleanup."""
        self._stop_token.cancel()
        super().stop()

    def _cleanup_connection(self) -> None:
        manager, self.analytics_manager = self.analytics_manager, None
        self.device_config = None
        self._connected = False
        if manager is not None:
            try:
                manager.stop()
            except Exception as e:
                logger.error(self._diagnostics.redact(f"Error stopping analytics manager: {e}"))
        with self._message_lock:
            dropped, self.latest_message = self.latest_message, None
        if dropped is not None:
            logger.warning(
                f"Dropping undecoded MQTT message on stop: source={self.source_id!r}, topic={dropped.topic!r}"
            )


class MQTTOverlaySource(OverlaySource):
    """MQTT-based overlay source for real-time analytics."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        broker_username: str,
        broker_password: str,
        device: DeviceConnection,
        analytics_data_source_key: str,
        decoder: PayloadToSceneDecoder,
        handler_type: str,
        source_id: str = "mqtt_overlay",
        parent: QObject | None = None,
        filter_factory: FilterFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_id = source_id
        self._handler_type = handler_type
        self.decoder = decoder
        self._filter_factory = filter_factory

        # Create worker thread
        self.worker = MQTTWorker(
            broker_host=broker_host,
            broker_port=broker_port,
            broker_username=broker_username,
            broker_password=broker_password,
            device=device,
            analytics_data_source_key=analytics_data_source_key,
            decoder=self.decoder,
            source_id=source_id,
        )

        self._own_worker(self.worker)

        # Connect worker signals
        self.worker.sourceError.connect(self.sourceError.emit)
        self.worker.sourceConnected.connect(self.sourceConnected.emit)
        self.worker.sourceReconnecting.connect(self.sourceReconnecting.emit)
        self.worker.set_overlay_ready_signal(self.overlayReady)

    @property
    def handler_type(self) -> str:
        """Return the configured overlay decoder handler."""
        return self._handler_type

    def play(self) -> bool:
        """Start overlay production."""
        logger.debug(f"[{self.source_id}] Starting MQTT overlay source")
        if self._delete_requested:
            return False
        return self.worker.play()

    def pause(self) -> None:
        """Pause overlay production."""
        logger.debug(f"[{self.source_id}] Pausing MQTT overlay source")
        self.worker.pause()

    def stop(self) -> None:
        """Request cancellation; the worker closes the transport before finishing."""
        logger.debug(f"[{self.source_id}] Stopping MQTT overlay source")
        self.worker.set_overlay_ready_signal(None)
        self.worker.request_stop()
        self.cleanup_posted_events()

    def wait(self, timeout_ms: int = 2000) -> bool:
        """Wait for overlay production to complete."""
        return bool(self.worker.wait(timeout_ms))

    def get_filter_config(self) -> FilterConfig:
        """Return the filter configuration for this overlay source."""
        if self._filter_factory is not None:
            return self._filter_factory()
        return build_default_filter_config()
