"""Fixtures shared by the tests in this folder."""

from __future__ import annotations

import copy
from collections.abc import Callable, Generator
from threading import Event
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import JobContext, JobScope
from tests.helpers.add_dialogs import Discovery, dialog_context, use_defaults


@pytest.fixture
def live_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog.default_resolution_context",
        dialog_context,
    )


@pytest.fixture
def _stub_live_stream_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved_defaults: dict[str, Any] = {
        "device": {
            "host": "camera.local",
            "username": "root",
            "password": "",
        },
        "live_stream": {
            "rtsp": {
                "camera_head": 1,
                "resolution": "1280x720",
                "data_stream_handler": "ONVIF_XML",
            },
            "analytics-mqtt": {
                "broker_host": "broker.local",
                "broker_username": "mqtt-user",
                "broker_password": "",
                "broker_port": 1883,
                "data_source_key": "analytics/source",
                "data_stream_handler": "ADF_BETA_FRAME",
            },
            "analytics-websocket": {
                "topic": "com.axis.scene.frame.v1",
                "channel_id": 2,
                "data_stream_handler": "ADF_BETA_FRAME",
            },
            "overlay_source": "none",
        },
    }
    raw_defaults: dict[str, Any] = copy.deepcopy(resolved_defaults)
    raw_defaults["device"]["host"] = "$AX_DEVIL_TARGET_ADDR"
    raw_defaults["device"]["username"] = "$AX_DEVIL_TARGET_USER"
    raw_defaults["device"]["password"] = "$AX_DEVIL_TARGET_PASS"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_host"] = "$AX_DEVIL_MQTT_BROKER_ADDR"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_username"] = "$AX_DEVIL_MQTT_BROKER_USER"
    raw_defaults["live_stream"]["analytics-mqtt"]["broker_password"] = "$AX_DEVIL_MQTT_BROKER_PASS"
    # The environment the references name holds the resolved defaults.
    for variable, value in (
        ("AX_DEVIL_TARGET_ADDR", "camera.local"),
        ("AX_DEVIL_TARGET_USER", "root"),
        ("AX_DEVIL_MQTT_BROKER_ADDR", "broker.local"),
        ("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user"),
    ):
        monkeypatch.setenv(variable, value)
    for variable in ("AX_DEVIL_TARGET_PASS", "AX_DEVIL_MQTT_BROKER_PASS"):
        monkeypatch.delenv(variable, raising=False)
    use_defaults(monkeypatch, raw_defaults, resolved_defaults)


@pytest.fixture
def discovery(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> Generator[Discovery, None, None]:
    """Fake the device and hold each discovery job instead of fabricating the loader's private state."""
    pending = Discovery(qtbot)
    submit = JobScope.submit

    def held_submit(
        scope: JobScope,
        name: str,
        work: Callable[[JobContext[Any]], Any],
        *,
        on_result: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[Any], None] | None = None,
        on_discard: Callable[[Any], None] | None = None,
    ) -> Callable[[], None]:
        if name == "load resolutions":  # Not one of the discovery requests whose order the tests steer.
            return submit(scope, name, work, on_result=on_result, on_error=on_error, on_progress=on_progress)
        go, delivered = Event(), list[bool]()
        pending.jobs.append((go, delivered))

        def held(job: JobContext[Any]) -> Any:
            assert go.wait(5)
            return work(job)

        def answer(callback: Callable[[Any], None], outcome: Any) -> None:
            delivered.append(True)
            callback(outcome)

        return submit(
            scope,
            name,
            held,
            on_result=lambda outcome: answer(on_result, outcome),
            on_error=lambda error: answer(on_error, error),
            on_progress=on_progress,
            on_discard=on_discard,
        )

    monkeypatch.setattr(JobScope, "submit", held_submit)
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.live_transport_pages.list_analytics_data_source_keys",
        lambda *_args: pending.sources,
    )
    monkeypatch.setattr(
        "ax_devil.modules.workspace.ui.add_content.live_transport_pages.list_datahub_topics",
        lambda *_args: pending.topics,
    )
    yield pending
    pending.release_all()
