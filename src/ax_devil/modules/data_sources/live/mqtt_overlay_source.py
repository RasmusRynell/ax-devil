"""MQTT-based overlay source implementation.

Real-time overlay streaming via MQTT with device connection support. Uses Worker pattern for background threading and
lifecycle management.
"""

import time
from typing import Any, Optional

from ax_devil_device_api import DeviceConfig
from ax_devil_mqtt import AxisAnalyticsMqttClient, MqttMessage
from PySide6.QtCore import QObject

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger

from ..base import OverlaySource, Worker
from .mqtt_discovery import list_mqtt_data_source_keys

logger = get_logger(__name__)


class UnsupportedAnalyticsDataSourceError(ValueError):
    """Raised when a configured analytics source is not available on the device."""


class MQTTWorker(Worker):
    """Worker thread for MQTT message processing."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        broker_username: str,
        broker_password: str,
        device_host: str,
        device_username: str,
        device_password: str,
        device_api_protocol: str,
        analytics_data_source_key: str,
        decoder: PayloadToSceneDecoder,
        source_id: str = "mqtt_overlay",
    ):
        super().__init__(source_id)

        self.broker_host = broker_host
        self.broker_port = broker_port
        self.broker_username = broker_username
        self.broker_password = broker_password
        self.device_host = device_host
        self.device_username = device_username
        self.device_password = device_password
        self.device_api_protocol = device_api_protocol
        self.analytics_data_source_key = analytics_data_source_key
        self.decoder = decoder

        self.analytics_manager: Optional[AxisAnalyticsMqttClient] = None
        self.device_config: Optional[DeviceConfig] = None
        self.latest_message: Optional[MqttMessage] = None
        self.message_count = 0
        self._connected = False

        # Signal to parent overlay source
        self.overlay_ready_signal: Optional[Any] = None

    def set_overlay_ready_signal(self, signal: Any) -> None:
        """Set the signal to emit when overlay is ready."""
        self.overlay_ready_signal = signal

    def run_loop(self) -> bool:
        """Main processing loop - called by Worker base class."""
        if not self._connected:
            self._connect()

        if not self._connected:
            time.sleep(0.1)  # Wait before retry
            return True

        # Process latest MQTT message if available
        if self.latest_message and self.overlay_ready_signal:
            try:
                self.message_count += 1
                scene = self.decoder.decode(self.latest_message.payload)
                if scene is None:
                    logger.debug("Decoder returned no scene for latest MQTT payload.")
                    self.latest_message = None
                    return True

                if not scene.time_slice.is_instant:
                    logger.warning(
                        "Non-instant time slices are not supported for streamed data,\
                              things might not work correctly! be careful"
                    )

                if isinstance(scene.time_slice.start, int):
                    raise ValueError("Streamed data with ints as timestamps is not supported at this moment")

                capture_timestamp = scene.time_slice.start.timestamp()

                # Create FrameIdentifier using device-provided timestamp from MQTT
                frame_id = FrameIdentifier(
                    sequence_id=self.message_count,
                    timestamp_monotime_us=capture_timestamp * 1000000.0,  # Convert seconds to microseconds
                )

                overlay_data = OverlayData(
                    content=scene,
                    frame_id=frame_id,
                    source_id=self.source_id,
                    metadata={"mqtt_message": self.latest_message},
                )

                self.overlay_ready_signal.emit(overlay_data)
                self.latest_message = None  # Clear processed message

            except Exception as e:
                import traceback

                logger.debug(traceback.format_exc())
                logger.error(f"Error decoding MQTT message: {e}")

        # Small sleep to prevent busy waiting
        time.sleep(0.01)  # 10ms poll rate
        return True

    def _connect(self) -> None:
        """Establish connection to device and MQTT broker."""
        try:
            logger.info(f"Connecting to device at {self.device_host}")

            if self.device_api_protocol == "https":
                self.device_config = DeviceConfig.https(
                    host=self.device_host,
                    username=self.device_username,
                    password=self.device_password,
                )
            else:
                self.device_config = DeviceConfig.http(
                    host=self.device_host,
                    username=self.device_username,
                    password=self.device_password,
                )

            self._validate_analytics_data_source()

            self.analytics_manager = AxisAnalyticsMqttClient(
                broker_host=self.broker_host,
                broker_port=self.broker_port,
                device_config=self.device_config,
                analytics_data_source_key=self.analytics_data_source_key,
                message_callback=self._on_message_received,
                broker_username=self.broker_username,
                broker_password=self.broker_password,
            )

            self.analytics_manager.start()  # Raise exception on failure
            self._connected = True
            logger.info("MQTT connection established")
            self.sourceConnected.emit()

        except UnsupportedAnalyticsDataSourceError as e:
            logger.error(str(e))
            self.sourceError.emit(str(e))
            self._connected = False
            super().stop()
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            self.sourceReconnecting.emit(f"Connection failed: {str(e)}")
            self._connected = False

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
            f"{self.device_host}. Available sources: {available_text}. Choose a source in Add Live Stream, or pass "
            "--data-source with a matching --handler-type."
        )

    def _on_message_received(self, message: MqttMessage) -> None:
        """Callback for new MQTT messages."""
        # Store latest message for processing in thread
        self.latest_message = message

    def request_stop(self) -> None:
        """Request worker-thread termination without racing connection setup cleanup."""
        super().stop()

    def stop(self) -> None:
        """Stop and cleanup MQTT connection."""
        self.request_stop()

        if self.analytics_manager:
            try:
                self.analytics_manager.stop()
            except Exception as e:
                logger.error(f"Error stopping analytics manager: {e}")
            finally:
                self.analytics_manager = None
                self.device_config = None
                self._connected = False


class MQTTOverlaySource(OverlaySource):
    """MQTT-based overlay source for real-time analytics."""

    def __init__(
        self,
        broker_host: str,
        broker_port: int,
        broker_username: str,
        broker_password: str,
        device_host: str,
        device_username: str,
        device_password: str,
        device_api_protocol: str,
        analytics_data_source_key: str,
        decoder: PayloadToSceneDecoder,
        handler_type: str,
        source_id: str = "mqtt_overlay",
        parent: Optional[QObject] = None,
        filter_factory: FilterFactory | None = None,
    ):
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
            device_host=device_host,
            device_username=device_username,
            device_password=device_password,
            device_api_protocol=device_api_protocol,
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
        return self._handler_type

    def play(self) -> bool:
        """Start overlay production."""
        logger.debug(f"[{self.source_id}] Starting MQTT overlay source")
        return self.worker.play()

    def pause(self) -> None:
        """Pause overlay production."""
        logger.debug(f"[{self.source_id}] Pausing MQTT overlay source")
        self.worker.pause()

    def stop(self) -> None:
        """Stop overlay production."""
        logger.debug(f"[{self.source_id}] Stopping MQTT overlay source")
        self.worker.set_overlay_ready_signal(None)
        self.worker.request_stop()
        self.worker.wait()

        # Connection setup has finished, so manager cleanup cannot race start().
        self.worker.stop()
        self.cleanup_posted_events()

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for overlay production to complete."""
        return bool(self.worker.wait(timeout))

    def get_filter_config(self) -> FilterConfig:
        """Return the filter configuration for this overlay source."""
        if self._filter_factory is not None:
            return self._filter_factory()
        return build_default_filter_config()
