"""Qt source and worker for live Axis DataHub overlays."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.filtering import FilterConfig, FilterFactory, build_default_filter_config
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.settings.logging_config import get_logger

from ..base import OverlaySource, Worker
from .datahub_client import DataHubTopicSample, DataHubWebSocketClient, DataHubWebSocketError

logger = get_logger(__name__)


class WebSocketWorker(Worker):
    """Worker thread for Axis DataHub WebSocket samples."""

    def __init__(
        self,
        *,
        topic: str,
        channel_id: int,
        device_host: str,
        device_username: str,
        device_password: str,
        device_api_protocol: str,
        decoder: PayloadToSceneDecoder,
        source_id: str = "websocket_overlay",
        client_factory: Callable[..., DataHubWebSocketClient] | None = None,
    ) -> None:
        super().__init__(source_id)
        self.topic = topic
        self.channel_id = channel_id
        self.device_host = device_host
        self.device_username = device_username
        self.device_password = device_password
        self.device_api_protocol = device_api_protocol
        self.decoder = decoder
        self.client_factory = client_factory or DataHubWebSocketClient
        self.client: DataHubWebSocketClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self.message_count = 0
        self.overlay_ready_signal: Any | None = None

    def set_overlay_ready_signal(self, signal: Any | None) -> None:
        """Set the signal to emit when an overlay is decoded."""
        self.overlay_ready_signal = signal

    def run_loop(self) -> bool:
        """Satisfy the Worker contract; the WebSocket loop is asynchronous."""
        return False

    def request_stop(self) -> None:
        """Request asynchronous loop termination before waiting for cleanup."""
        super().stop()
        loop = self._loop
        task = self._task
        if loop is not None and task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                logger.debug("DataHub WebSocket event loop already closed during stop")

    def run(self) -> None:
        """Run the async WebSocket client in the worker's QThread."""
        try:
            asyncio.run(self._run_async())
        finally:
            self._running = False
            self.sourceFinished.emit()

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        first_sample_warning: asyncio.TimerHandle | None = None
        try:
            if not self._is_running():
                return
            self.client = self.client_factory(
                host=self.device_host,
                username=self.device_username,
                password=self.device_password,
                protocol=self.device_api_protocol,
                topic=self.topic,
                channel_id=self.channel_id,
            )
            await self.client.connect()
            self.sourceConnected.emit()
            first_sample_warning = self._loop.call_later(
                10.0,
                logger.warning,
                f"No DataHub samples received for topic {self.topic!r} on channel {self.channel_id}.",
            )
            while self._is_running():
                sample = await self.client.receive_sample()
                first_sample_warning.cancel()
                if self._is_paused():
                    continue
                self._decode_sample(sample)
        except asyncio.CancelledError:
            return
        except DataHubWebSocketError as exc:
            if self._is_running():
                logger.error(str(exc))
                self.sourceError.emit(str(exc))
        except Exception as exc:
            if self._is_running():
                logger.error(f"DataHub WebSocket processing failed: {type(exc).__name__}")
                self.sourceError.emit(f"DataHub WebSocket processing failed: {type(exc).__name__}.")
        finally:
            if first_sample_warning is not None:
                first_sample_warning.cancel()
            try:
                if self.client is not None:
                    await self.client.close()
                    self.client = None
            finally:
                self._task = None
                self._loop = None

    def _decode_sample(self, sample: DataHubTopicSample) -> None:
        if self.overlay_ready_signal is None:
            return

        try:
            self.message_count += 1
            scene = self.decoder.decode(sample.data)
            if scene is None:
                logger.debug("Decoder returned no scene for DataHub WebSocket payload.")
                return

            if not scene.time_slice.is_instant:
                logger.warning(
                    "Non-instant time slices are not supported for streamed data, "
                    "things might not work correctly! be careful"
                )

            if isinstance(scene.time_slice.start, int):
                raise ValueError("Streamed data with ints as timestamps is not supported at this moment")

            capture_timestamp = scene.time_slice.start.timestamp()
            frame_id = FrameIdentifier(
                sequence_id=self.message_count,
                timestamp_monotime_us=capture_timestamp * 1000000.0,
            )
            overlay_data = OverlayData(
                content=scene,
                frame_id=frame_id,
                source_id=self.source_id,
                metadata={
                    "websocket_topic": sample.topic,
                    "websocket_channel_id": sample.channel_id,
                    "websocket_timestamp": sample.timestamp,
                    "websocket_is_historical": sample.is_historical,
                },
            )
            self.overlay_ready_signal.emit(overlay_data)
        except Exception as exc:
            logger.error(f"Error decoding DataHub WebSocket message: {type(exc).__name__}")

    def _is_running(self) -> bool:
        self._mutex.lock()
        try:
            return self._running
        finally:
            self._mutex.unlock()

    def _is_paused(self) -> bool:
        self._mutex.lock()
        try:
            return self._paused
        finally:
            self._mutex.unlock()


class WebSocketOverlaySource(OverlaySource):
    """Axis DataHub WebSocket overlay source for real-time analytics."""

    def __init__(
        self,
        *,
        topic: str,
        channel_id: int,
        device_host: str,
        device_username: str,
        device_password: str,
        device_api_protocol: str,
        decoder: PayloadToSceneDecoder,
        handler_type: str,
        source_id: str = "websocket_overlay",
        parent: QObject | None = None,
        filter_factory: FilterFactory | None = None,
        client_factory: Callable[..., DataHubWebSocketClient] | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_id = source_id
        self._handler_type = handler_type
        self._filter_factory = filter_factory
        self.worker = WebSocketWorker(
            topic=topic,
            channel_id=channel_id,
            device_host=device_host,
            device_username=device_username,
            device_password=device_password,
            device_api_protocol=device_api_protocol,
            decoder=decoder,
            source_id=source_id,
            client_factory=client_factory,
        )
        self._own_worker(self.worker)
        self.worker.sourceError.connect(self.sourceError.emit)
        self.worker.sourceConnected.connect(self.sourceConnected.emit)
        self.worker.set_overlay_ready_signal(self.overlayReady)

    @property
    def handler_type(self) -> str:
        """Return the decoder handler identifier."""
        return self._handler_type

    def play(self) -> bool:
        """Start overlay production."""
        logger.debug(f"[{self.source_id}] Starting DataHub WebSocket overlay source")
        self.worker.set_overlay_ready_signal(self.overlayReady)
        return self.worker.play()

    def pause(self) -> None:
        """Pause overlay production while draining incoming samples."""
        logger.debug(f"[{self.source_id}] Pausing DataHub WebSocket overlay source")
        self.worker.pause()

    def stop(self) -> None:
        """Stop the worker and close its WebSocket before returning."""
        logger.debug(f"[{self.source_id}] Stopping DataHub WebSocket overlay source")
        self.worker.set_overlay_ready_signal(None)
        self.worker.request_stop()
        if not self.worker.wait(2000):
            logger.warning(f"[{self.source_id}] WebSocket worker did not stop within 2000 ms")
        self.cleanup_posted_events()

    def wait(self, timeout: int = 2000) -> bool:
        """Wait for overlay production to complete."""
        return bool(self.worker.wait(timeout))

    def get_filter_config(self) -> FilterConfig:
        """Return the filter configuration for this overlay source."""
        if self._filter_factory is not None:
            return self._filter_factory()
        return build_default_filter_config()
