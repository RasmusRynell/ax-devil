"""Tests for the Axis DataHub WebSocket overlay protocol."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from multidict import CIMultiDict

from ax_devil.modules.data_sources.live.datahub_client import (
    DataHubTopicSample,
    DataHubWebSocketClient,
    DataHubWebSocketError,
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
    """Replay JSON messages or transport errors, then idle until cancelled or repeat a final notification."""

    def __init__(self, messages: list[dict[str, object] | Exception], repeat: dict[str, object] | None = None) -> None:
        self.sent: list[dict[str, object]] = []
        self._messages = messages
        self._repeat = repeat
        self.closed = False

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    async def receive(self, timeout: float | None = None) -> SimpleNamespace:
        del timeout
        if self._messages:
            message = self._messages.pop(0)
        elif self._repeat is not None:
            await asyncio.sleep(0)
            message = self._repeat
        else:
            await asyncio.Future[None]()
        if isinstance(message, Exception):
            raise message
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(message))

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


def _client(session: _FakeSession, *, topic: str | None = None, protocol: str = "https") -> DataHubWebSocketClient:
    return DataHubWebSocketClient(
        host="camera.local",
        username="root",
        password="pass",
        protocol=protocol,
        topic=topic,
        channel_id=None if topic is None else 1,
        session_factory=lambda: session,
    )


def test_request_timeout_is_not_extended_by_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device that only sends status notifications cannot hold a request open forever."""
    monkeypatch.setattr(DataHubWebSocketClient, "_request_timeout", 0.05)
    session = _FakeSession(_FakeWebSocket([], repeat={"jsonrpc": "2.0", "method": "data-hub_v1:status"}))
    client = _client(session)

    async def exercise() -> None:
        await client.connect()
        try:
            await client.list_topics()
        finally:
            await client.close()

    with pytest.raises(DataHubWebSocketError, match="Timed out.*Last server notification: data-hub_v1:status"):
        asyncio.run(exercise())


def test_client_session_has_bounded_handshake_read_timeout() -> None:
    """A device that accepts the connection but never answers cannot stall the handshake forever."""
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
            assert session.timeout.sock_read is not None
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
def test_client_subscribes_and_keeps_samples_that_arrive_before_the_response(
    protocol: str, websocket_scheme: str
) -> None:
    """Both device protocols subscribe to one channel and deliver samples sent before the subscribe reply."""
    websocket = _FakeWebSocket(
        [
            {"jsonrpc": "2.0", "method": "data-hub_v1:status", "params": {"state": "ready"}},
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
    client = _client(session, topic="topic", protocol=protocol)

    async def exercise() -> DataHubTopicSample:
        await client.connect()
        sample = await client.receive_sample()
        await client.close()
        await client.close()
        return sample

    sample = asyncio.run(exercise())

    assert sample.data == {"value": 1}
    assert websocket.sent[0]["method"] == "data-hub_v1:subscribe"
    assert websocket.sent[0]["params"] == {
        "filters": [{"topics": ["topic"], "instanceFilter": [{"channel_id": 1}]}],
        "startFrom": {"position": "new"},
    }
    assert session.requested_url == f"{protocol}://camera.local/axis-cgi/wssession.cgi"
    assert session.websocket_url.startswith(f"{websocket_scheme}://camera.local/")
    assert "headers" not in session.requested_kwargs
    assert "wssession=session-token" in session.websocket_url
    assert "apis=data-hub" in session.websocket_url
    assert session.closed is True
    assert websocket.closed is True


def test_client_uses_basic_when_device_advertises_basic() -> None:
    """Basic credentials are sent only after a camera requests Basic authentication."""
    websocket = _FakeWebSocket([])
    session = _FakeSession(
        websocket,
        responses=[
            _FakeResponse("", status=401, headers={"WWW-Authenticate": 'Basic realm="axis"'}),
            _FakeResponse("session-token"),
        ],
    )
    client = _client(session)

    async def exercise() -> None:
        await client.connect()
        await client.close()

    asyncio.run(exercise())

    assert "headers" not in session.requested_kwargs_history[0]
    assert session.requested_kwargs_history[1]["headers"] == {"Authorization": "Basic cm9vdDpwYXNz"}
    assert "middlewares" not in session.requested_kwargs_history[1]


def test_client_retries_with_digest_when_device_advertises_digest() -> None:
    """A Digest challenge is answered with Digest, never by sending Basic credentials."""
    websocket = _FakeWebSocket([])
    session = _FakeSession(
        websocket,
        responses=[
            _FakeResponse("", status=401, headers={"WWW-Authenticate": 'Digest realm="axis"'}),
            _FakeResponse("session-token"),
        ],
    )
    client = _client(session)

    async def exercise() -> None:
        await client.connect()
        await client.close()

    asyncio.run(exercise())

    assert all("headers" not in kwargs for kwargs in session.requested_kwargs_history)
    middlewares = session.requested_kwargs_history[1]["middlewares"]
    assert isinstance(middlewares, tuple)
    assert any(isinstance(middleware, aiohttp.DigestAuthMiddleware) for middleware in middlewares)


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
    client = _client(session, protocol="http")

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
    client = _client(session, protocol="http")

    with pytest.raises(DataHubWebSocketError, match="HTTP 401"):
        asyncio.run(client.connect())

    assert len(session.requested_kwargs_history) == 2
    if scheme == "Digest":
        assert all("headers" not in kwargs for kwargs in session.requested_kwargs_history)
    assert session.closed
    assert session.websocket_url == ""


def test_receive_sample_surfaces_permanent_socket_timeout() -> None:
    """A socket read timeout ends the receive loop with a DataHub error instead of hanging."""
    session = _FakeSession(_FakeWebSocket([aiohttp.SocketTimeoutError("socket timed out")]))
    client = _client(session)

    async def exercise() -> None:
        await client.connect()
        try:
            await client.receive_sample()
        finally:
            await client.close()

    with pytest.raises(DataHubWebSocketError, match="transport error"):
        asyncio.run(exercise())


def test_receive_sample_waits_through_idle_and_can_be_cancelled() -> None:
    """An idle device keeps the receiver waiting until it is cancelled, after which close still succeeds."""
    websocket = _FakeWebSocket([{"jsonrpc": "2.0", "method": "data-hub_v1:status"}])
    session = _FakeSession(websocket)
    client = _client(session)

    async def exercise() -> None:
        await client.connect()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.receive_sample(), timeout=0.05)
        await client.close()

    asyncio.run(exercise())
    assert websocket.closed
    assert session.closed


def test_client_rejects_json_rpc_error_response() -> None:
    websocket = _FakeWebSocket([{"jsonrpc": "2.0", "id": "ax-devil-1", "error": {"code": -32602, "message": "bad"}}])
    session = _FakeSession(websocket)
    client = _client(session, topic="topic")

    with pytest.raises(DataHubWebSocketError, match="error code -32602"):
        asyncio.run(client.connect())
    assert session.closed
