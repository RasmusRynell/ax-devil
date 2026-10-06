"""Tests for the Axis DataHub WebSocket overlay protocol."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from multidict import CIMultiDict

from ax_devil.modules.data_sources.live.datahub_client import (
    DataHubTopicSample,
    DataHubWebSocketClient,
    DataHubWebSocketError,
    build_subscribe_params,
    discover_datahub_topics,
    parse_topic_sample,
)


class _FakeResponse:
    def __init__(self, body: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = CIMultiDict(headers or {})

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self) -> str:
        return self._body

    async def read(self) -> bytes:
        return self._body.encode()


class _FakeWebSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.sent: list[dict[str, object]] = []
        self._messages = [SimpleNamespace(type=1, data=json.dumps(message)) for message in messages]
        self.closed = False

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    async def receive(self, timeout: float | None = None) -> SimpleNamespace:
        del timeout
        if not self._messages:
            await asyncio.Future[None]()
        return self._messages.pop(0)

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(
        self,
        websocket: _FakeWebSocket,
        token: str = "session-token",
        responses: list[_FakeResponse] | None = None,
    ) -> None:
        self.websocket = websocket
        self.token = token
        self.requested_url = ""
        self.requested_kwargs: dict[str, object] = {}
        self.requested_kwargs_history: list[dict[str, object]] = []
        self.responses = responses
        self.websocket_url = ""
        self.websocket_kwargs: dict[str, object] = {}
        self.closed = False

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.requested_url = url
        self.requested_kwargs = kwargs
        self.requested_kwargs_history.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return _FakeResponse(self.token)

    async def ws_connect(self, url: str, **kwargs: object) -> _FakeWebSocket:
        self.websocket_url = url
        self.websocket_kwargs = kwargs
        timeout = kwargs["timeout"]
        assert isinstance(timeout, aiohttp.ClientWSTimeout)
        assert timeout.ws_receive is None
        return self.websocket

    async def close(self) -> None:
        self.closed = True


def test_build_subscribe_params_uses_new_position_and_channel_filter() -> None:
    assert build_subscribe_params("com.axis.scene.frame.v1", 3) == {
        "filters": [
            {
                "topics": ["com.axis.scene.frame.v1"],
                "instanceFilter": [{"channel_id": 3}],
            }
        ],
        "startFrom": {"position": "new"},
    }


def test_client_request_timeout_is_not_extended_by_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    websocket = _FakeWebSocket([])
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic=None,
        channel_id=None,
    )
    client._websocket = websocket
    client._request_timeout = 0.001

    async def receive_notification() -> dict[str, object]:
        await asyncio.sleep(0)
        return {"method": "data-hub_v1:status"}

    monkeypatch.setattr(client, "_receive_json", receive_notification)

    with pytest.raises(DataHubWebSocketError, match="Timed out waiting"):
        asyncio.run(client._request(method="data-hub_v1:listTopics", params={}))


def test_client_reports_last_notification_when_request_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic=None,
        channel_id=None,
    )
    client._websocket = _FakeWebSocket([])
    client._request_timeout = 0.001

    async def receive_notification() -> dict[str, object]:
        await asyncio.sleep(0)
        return {"method": "data-hub_v1:status"}

    monkeypatch.setattr(client, "_receive_json", receive_notification)

    with pytest.raises(DataHubWebSocketError, match="Last server notification: data-hub_v1:status"):
        asyncio.run(client._request(method="data-hub_v1:listTopics", params={}))


def test_client_session_has_bounded_handshake_read_timeout() -> None:
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic=None,
        channel_id=None,
    )

    async def exercise() -> None:
        session = client._create_session()
        try:
            assert session.timeout.sock_read == 10
        finally:
            await session.close()

    asyncio.run(exercise())


def test_discover_datahub_topics_uses_list_topics_without_subscribing() -> None:
    websocket = _FakeWebSocket(
        [
            {
                "jsonrpc": "2.0",
                "id": "ax-devil-1",
                "result": {"topics": [{"name": "topic.one"}, {"name": "topic.two"}]},
            }
        ]
    )
    session = _FakeSession(websocket)

    async def exercise() -> tuple[str, ...]:
        return await discover_datahub_topics(
            host="camera.local",
            username="root",
            password="pass",
            protocol="https",
            session_factory=lambda: session,
        )

    assert asyncio.run(exercise()) == ("topic.one", "topic.two")
    assert websocket.sent == [
        {
            "jsonrpc": "2.0",
            "id": "ax-devil-1",
            "method": "data-hub_v1:listTopics",
            "params": {},
        }
    ]
    assert session.closed is True
    assert websocket.closed is True


def test_parse_topic_sample_extracts_datahub_envelope() -> None:
    sample = parse_topic_sample(
        {
            "jsonrpc": "2.0",
            "method": "data-hub_v1:topicSample",
            "params": {
                "topic": "com.axis.scene.frame.v1",
                "instance": {"channel_id": 2},
                "data": {"timestamp": "scene-time", "detections": []},
                "timestamp": "2026-03-17T09:39:52.569Z",
                "isHistorical": False,
            },
        }
    )

    assert sample == DataHubTopicSample(
        topic="com.axis.scene.frame.v1",
        channel_id=2,
        data={"timestamp": "scene-time", "detections": []},
        timestamp="2026-03-17T09:39:52.569Z",
        is_historical=False,
    )


def test_parse_topic_sample_rejects_invalid_data() -> None:
    with pytest.raises(DataHubWebSocketError, match="invalid data"):
        parse_topic_sample(
            {
                "method": "data-hub_v1:topicSample",
                "params": {
                    "topic": "topic",
                    "instance": {"channel_id": 1},
                    "data": [],
                    "timestamp": "now",
                    "isHistorical": False,
                },
            }
        )


@pytest.mark.parametrize(("protocol", "websocket_scheme"), [("https", "wss"), ("http", "ws")])
def test_client_correlates_subscribe_response_after_notification(protocol: str, websocket_scheme: str) -> None:
    """Both device protocols carry subscriptions and topic samples through the client."""
    websocket = _FakeWebSocket(
        [
            {
                "jsonrpc": "2.0",
                "method": "data-hub_v1:topicSample",
                "params": {
                    "topic": "topic",
                    "instance": {"channel_id": 1},
                    "data": {"value": 1},
                    "timestamp": "now",
                    "isHistorical": False,
                },
            },
            {"jsonrpc": "2.0", "id": "ax-devil-1", "result": {"subscriptionId": "1"}},
        ]
    )
    session = _FakeSession(websocket)
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol=protocol,
        topic="topic",
        channel_id=1,
        session_factory=lambda: session,
    )

    async def exercise() -> DataHubTopicSample | None:
        await client.connect()
        sample = await client.receive_sample()
        await client.close()
        return sample

    sample = asyncio.run(exercise())

    assert sample is not None
    assert sample.data == {"value": 1}
    assert websocket.sent[0]["method"] == "data-hub_v1:subscribe"
    assert session.requested_url == f"{protocol}://camera.local/axis-cgi/wssession.cgi"
    assert session.websocket_url.startswith(f"{websocket_scheme}://camera.local/")
    assert "headers" not in session.requested_kwargs
    assert "wssession=session-token" in session.websocket_url
    assert "apis=data-hub" in session.websocket_url
    assert session.closed is True
    assert websocket.closed is True


@pytest.mark.parametrize("protocol", ["https", "http"])
def test_client_uses_basic_when_device_advertises_basic(protocol: str) -> None:
    """Basic credentials are sent only after a camera requests Basic authentication."""
    websocket = _FakeWebSocket([{"jsonrpc": "2.0", "id": "ax-devil-1", "result": {}}])
    session = _FakeSession(
        websocket,
        responses=[
            _FakeResponse("", status=401, headers={"WWW-Authenticate": 'Basic realm="axis"'}),
            _FakeResponse("session-token"),
        ],
    )
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol=protocol,
        topic=None,
        channel_id=None,
        session_factory=lambda: session,
    )

    async def exercise() -> None:
        await client.connect()
        await client.close()

    asyncio.run(exercise())

    assert "headers" not in session.requested_kwargs_history[0]
    assert session.requested_kwargs_history[1]["headers"] == {"Authorization": "Basic cm9vdDpwYXNz"}
    assert "middlewares" not in session.requested_kwargs_history[1]


@pytest.mark.parametrize("protocol", ["https", "http"])
def test_client_retries_with_digest_when_device_advertises_digest(protocol: str) -> None:
    """Both transports select Digest without first sending Basic credentials."""
    websocket = _FakeWebSocket([{"jsonrpc": "2.0", "id": "ax-devil-1", "result": {}}])
    session = _FakeSession(
        websocket,
        responses=[
            _FakeResponse("", status=401, headers={"WWW-Authenticate": 'Digest realm="axis"'}),
            _FakeResponse("session-token"),
        ],
    )
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol=protocol,
        topic=None,
        channel_id=None,
        session_factory=lambda: session,
    )

    async def exercise() -> None:
        await client.connect()
        await client.close()

    asyncio.run(exercise())

    assert all("headers" not in kwargs for kwargs in session.requested_kwargs_history)
    assert all(kwargs["ssl"] is False for kwargs in session.requested_kwargs_history)
    assert session.websocket_kwargs["ssl"] is False
    assert isinstance(session.requested_kwargs_history[1]["middlewares"], tuple)


@pytest.mark.parametrize(
    ("status", "challenge"),
    [(403, 'Basic realm="axis"'), (401, ""), (401, 'Bearer realm="basic, digest"')],
)
def test_client_does_not_send_credentials_without_supported_challenge(status: int, challenge: str) -> None:
    """Failures and unsupported schemes do not cause credentials to be sent."""
    session = _FakeSession(
        _FakeWebSocket([]),
        responses=[_FakeResponse("", status=status, headers={"WWW-Authenticate": challenge})],
    )
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="http",
        topic=None,
        channel_id=None,
        session_factory=lambda: session,
    )

    with pytest.raises(DataHubWebSocketError, match="DataHub session request"):
        asyncio.run(client.connect())

    assert len(session.requested_kwargs_history) == 1
    assert "headers" not in session.requested_kwargs_history[0]
    assert "middlewares" not in session.requested_kwargs_history[0]
    assert session.closed
    assert session.websocket_url == ""


@pytest.mark.parametrize("scheme", ["Basic", "Digest"])
def test_client_stops_after_rejected_authentication(scheme: str) -> None:
    """Rejected credentials close the session without retrying a weaker scheme."""
    session = _FakeSession(
        _FakeWebSocket([]),
        responses=[
            _FakeResponse("", status=401, headers={"WWW-Authenticate": f'{scheme} realm="axis"'}),
            _FakeResponse("", status=401, headers={"WWW-Authenticate": 'Basic realm="axis"'}),
        ],
    )
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="http",
        topic=None,
        channel_id=None,
        session_factory=lambda: session,
    )

    with pytest.raises(DataHubWebSocketError, match="HTTP 401"):
        asyncio.run(client.connect())

    assert len(session.requested_kwargs_history) == 2
    if scheme == "Digest":
        assert all("headers" not in kwargs for kwargs in session.requested_kwargs_history)
    assert session.closed
    assert session.websocket_url == ""


def test_client_ignores_unrelated_notification_before_response() -> None:
    websocket = _FakeWebSocket(
        [
            {"jsonrpc": "2.0", "method": "data-hub_v1:status", "params": {"state": "ready"}},
            {"jsonrpc": "2.0", "id": "ax-devil-1", "result": {"subscriptionId": "1"}},
        ]
    )
    session = _FakeSession(websocket)
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic="topic",
        channel_id=1,
        session_factory=lambda: session,
    )

    async def exercise() -> None:
        await client.connect()
        await client.close()

    asyncio.run(exercise())


def test_receive_sample_surfaces_permanent_socket_timeout() -> None:
    client = DataHubWebSocketClient(
        host="camera.local", username="root", password="pass", protocol="https", topic=None, channel_id=None
    )
    client._websocket = MagicMock()
    client._websocket.receive = AsyncMock(side_effect=aiohttp.SocketTimeoutError("socket timed out"))

    with pytest.raises(DataHubWebSocketError, match="transport error"):
        asyncio.run(client.receive_sample())


def test_receive_sample_waits_through_idle_and_can_be_cancelled() -> None:
    websocket = _FakeWebSocket([{"method": "data-hub_v1:status"}])
    client = DataHubWebSocketClient(
        host="camera.local", username="root", password="pass", protocol="https", topic=None, channel_id=None
    )
    client._websocket = websocket

    async def exercise() -> None:
        task = asyncio.create_task(client.receive_sample())
        await asyncio.sleep(0.02)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.close()

    asyncio.run(exercise())
    assert websocket.closed


def test_client_rejects_json_rpc_error_response() -> None:
    websocket = _FakeWebSocket([{"jsonrpc": "2.0", "id": "ax-devil-1", "error": {"code": -32602, "message": "bad"}}])
    session = _FakeSession(websocket)
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic="topic",
        channel_id=1,
        session_factory=lambda: session,
    )

    with pytest.raises(DataHubWebSocketError, match="error code -32602"):
        asyncio.run(client.connect())


def test_client_close_is_idempotent() -> None:
    client = DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol="https",
        topic="topic",
        channel_id=1,
        session_factory=MagicMock(),
    )

    asyncio.run(client.close())
    asyncio.run(client.close())
