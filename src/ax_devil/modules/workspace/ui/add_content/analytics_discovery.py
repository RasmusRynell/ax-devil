"""Workspace analytics discovery state and background-job adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QObject, Signal

from ax_devil.core.device_endpoint import DeviceEndpoint, DeviceProtocol
from ax_devil.modules.background_jobs import BackgroundJobs, JobContext, LatestRequest, background_jobs
from ax_devil.modules.data_sources.live.datahub_client import discover_datahub_topics
from ax_devil.modules.data_sources.live.mqtt_discovery import list_mqtt_data_source_keys
from ax_devil.modules.settings.config_manager import builtin_default


@dataclass(frozen=True)
class DeviceConnection:
    """The device and credentials analytics choices are discovered from."""

    endpoint: DeviceEndpoint
    username: str
    password: str
    timeout_s: float


FetchChoices = Callable[[DeviceConnection], tuple[str, ...]]


def _fetch_choices(fetch: FetchChoices, connection: DeviceConnection, _job: JobContext[None]) -> tuple[str, ...]:
    return fetch(connection)


class AnalyticsChoiceLoader(QObject):
    """Own discovery state and discard results for an outdated device connection.

    *fetch* runs as one of *jobs*, the application's "discovery" pool unless given: a device request cannot be
    interrupted, so one abandoned by closing the dialog keeps its worker until it times out, away from the shared pool.
    """

    changed = Signal()
    connection_changed = Signal()

    def __init__(
        self, fetch: FetchChoices, label: str, parent: QObject | None = None, *, jobs: BackgroundJobs | None = None
    ) -> None:
        super().__init__(parent)
        self.label = label
        self.choices: tuple[str, ...] = ()
        self.loaded = False
        self.status = f"Select overlay mode to load {label}"
        self._fetch = fetch
        self._connection = DeviceConnection(
            DeviceEndpoint("", DeviceProtocol(builtin_default("defaults.device.protocol"))),
            "",
            "",
            builtin_default("defaults.device.connection_timeout_s"),
        )
        self._request = LatestRequest(self)
        self._jobs = (jobs or background_jobs("discovery")).scope(self)
        self._closed = False

    @property
    def loading(self) -> bool:
        """Return whether choices for the current connection are being fetched."""
        return self._request.pending

    def set_connection(self, connection: DeviceConnection) -> None:
        """Invalidate choices when the device or credentials change."""
        if connection == self._connection:
            return
        self._request.cancel()
        self._connection = connection
        self.loaded = False
        self.choices = ()
        self.status = f"Connection changed — refresh {self.label}"
        self.connection_changed.emit()
        self.changed.emit()

    def load(self) -> None:
        """Fetch choices for the current connection without blocking the UI."""
        if self.loading or self._closed:
            return
        if not self._connection.endpoint.host:
            self.status = f"Enter a device host to load {self.label}"
            self.changed.emit()
            return
        connection = self._connection
        self.loaded = False
        self.choices = ()
        self.status = f"Loading {self.label}…"
        self._request.submit(
            self._jobs,
            f"load {self.label}",
            partial(_fetch_choices, self._fetch, connection),
            on_result=self._on_loaded,
            on_error=self._on_failed,
        )
        self.changed.emit()

    def _on_loaded(self, choices: tuple[str, ...]) -> None:
        self.loaded = True
        self.choices = choices
        self.status = f"Select {self.label}" if choices else f"No {self.label} available"
        self.changed.emit()

    def _on_failed(self, _error: Exception) -> None:
        self.status = f"Could not load {self.label}"
        self.changed.emit()

    def cleanup(self) -> None:
        """Disconnect this loader from any in-flight result."""
        self._closed = True
        self._request.close()
        self._jobs.close()


def list_datahub_topics(connection: DeviceConnection) -> tuple[str, ...]:
    """Return available DataHub topic names from a device."""
    return asyncio.run(
        discover_datahub_topics(
            endpoint=connection.endpoint,
            username=connection.username,
            password=connection.password,
            timeout_s=connection.timeout_s,
        )
    )


def list_analytics_data_source_keys(connection: DeviceConnection) -> tuple[str, ...]:
    """Return available analytics data-source keys from a device."""
    return list_mqtt_data_source_keys(
        connection.endpoint.device_config(connection.username, connection.password, timeout_s=connection.timeout_s)
    )
