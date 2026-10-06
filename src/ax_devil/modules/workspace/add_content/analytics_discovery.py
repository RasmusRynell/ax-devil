"""Workspace analytics discovery state and background-job adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from ax_devil_device_api import DeviceConfig
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ax_devil.modules.data_sources.live.datahub_client import discover_datahub_topics
from ax_devil.modules.data_sources.live.mqtt_discovery import list_mqtt_data_source_keys
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)
DeviceConnection = tuple[str, str, str, str]
FetchChoices = Callable[[str, str, str, str], tuple[str, ...]]


class _LoadSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _LoadJob(QRunnable):
    def __init__(self, load: Callable[[], tuple[str, ...]]) -> None:
        super().__init__()
        self.signals = _LoadSignals()
        self._load = load

    def run(self) -> None:
        try:
            self.signals.loaded.emit(self._load())
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class AnalyticsChoiceLoader(QObject):
    """Own discovery state and discard results for an outdated device connection."""

    changed = Signal()

    def __init__(self, fetch: FetchChoices, label: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self.choices: tuple[str, ...] = ()
        self.loaded = False
        self.loading = False
        self.status = f"Select overlay mode to load {label}"
        self._fetch = fetch
        self._connection: DeviceConnection = ("", "", "", "https")
        self._request: DeviceConnection | None = None
        self._job: _LoadJob | None = None
        self._closed = False

    def set_connection(self, connection: DeviceConnection) -> None:
        """Invalidate choices when the device or credentials change."""
        if connection == self._connection:
            return
        self._connection = connection
        self.loaded = False
        self.choices = ()
        if self._request is not None:
            self.status = f"Connection changed — refresh {self.label}"
        self.changed.emit()

    def load(self) -> None:
        """Fetch choices for the current connection without blocking the UI."""
        if self._job is not None or self._closed:
            return
        if not self._connection[0]:
            self.status = f"Enter a device host to load {self.label}"
            self.changed.emit()
            return
        connection = self._connection
        self._request = connection
        self._job = _LoadJob(lambda: self._fetch(*connection))
        self._job.signals.loaded.connect(self._on_loaded)
        self._job.signals.failed.connect(self._on_failed)
        self._job.signals.finished.connect(self._on_finished)
        self.loading = True
        self.loaded = False
        self.choices = ()
        self.status = f"Loading {self.label}…"
        self.changed.emit()
        QThreadPool.globalInstance().start(self._job)

    def _on_loaded(self, choices: tuple[str, ...]) -> None:
        if self._closed or self._request != self._connection:
            return
        self.loaded = True
        self.choices = choices
        self.status = f"Select {self.label}" if choices else f"No {self.label} available"
        self.changed.emit()

    def _on_failed(self, message: str) -> None:
        if self._closed or self._request != self._connection:
            return
        logger.warning(f"Failed to load {self.label}: {message}")
        self.status = f"Could not load {self.label}"
        self.changed.emit()

    def _on_finished(self) -> None:
        self._job = None
        self.loading = False
        self.changed.emit()

    def cleanup(self) -> None:
        """Disconnect this loader from any in-flight result."""
        self._closed = True
        if self._job is not None:
            self._job.signals.loaded.disconnect(self._on_loaded)
            self._job.signals.failed.disconnect(self._on_failed)
            self._job.signals.finished.disconnect(self._on_finished)
            self._job = None


def list_datahub_topics(host: str, username: str, password: str, protocol: str) -> tuple[str, ...]:
    """Return available DataHub topic names from a device."""
    return asyncio.run(
        discover_datahub_topics(
            host=host,
            username=username,
            password=password,
            protocol=protocol,
        )
    )


def list_analytics_data_source_keys(host: str, username: str, password: str, protocol: str) -> tuple[str, ...]:
    """Return available analytics data-source keys from a device."""
    config_factory = DeviceConfig.https if protocol == "https" else DeviceConfig.http
    config = config_factory(host=host, username=username, password=password)
    return list_mqtt_data_source_keys(config)
