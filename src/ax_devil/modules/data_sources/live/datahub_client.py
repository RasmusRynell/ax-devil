"""Axis DataHub authentication, discovery, and WebSocket protocol."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import count
from typing import Any
from urllib.parse import urlencode
from urllib.request import parse_http_list

import aiohttp
from aiohttp import WSMsgType

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

JsonObject = dict[str, Any]
SessionFactory = Callable[[], Any]


class DataHubWebSocketError(RuntimeError):
    """Raised when the DataHub WebSocket protocol or transport fails."""


async def _reject_redirects(
    request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
) -> aiohttp.ClientResponse:
    response = await handler(request)
    if 300 <= response.status < 400:
        response.close()
        raise DataHubWebSocketError("DataHub redirects are not allowed. Connect directly to the device host.")
    return response


@dataclass(frozen=True, slots=True)
class DataHubTopicSample:
    """Decoded envelope for one DataHub topic sample."""

    topic: str
    channel_id: int
    data: dict[str, Any]
    timestamp: str
    is_historical: bool


def build_subscribe_params(topic: str, channel_id: int) -> JsonObject:
    """Build the one-topic, one-channel DataHub subscription parameters."""
    return {
        "filters": [
            {
                "topics": [topic],
                "instanceFilter": [{"channel_id": channel_id}],
            }
        ],
        "startFrom": {"position": "new"},
    }


def parse_topic_sample(message: Mapping[str, Any]) -> DataHubTopicSample:
    """Validate and decode a DataHub topic-sample notification."""
    if message.get("method") != "data-hub_v1:topicSample":
        raise DataHubWebSocketError("Unexpected DataHub WebSocket notification method.")

    params = message.get("params")
    if not isinstance(params, dict):
        raise DataHubWebSocketError("DataHub topic-sample notification has invalid params.")

    topic = params.get("topic")
    instance = params.get("instance")
    data = params.get("data")
    timestamp = params.get("timestamp")
    is_historical = params.get("isHistorical")
    channel_id = instance.get("channel_id") if isinstance(instance, dict) else None

    if not isinstance(topic, str) or not topic:
        raise DataHubWebSocketError("DataHub topic-sample notification has no topic.")
    if not isinstance(channel_id, int) or isinstance(channel_id, bool):
        raise DataHubWebSocketError("DataHub topic-sample notification has no valid channel_id.")
    if not isinstance(data, dict):
        raise DataHubWebSocketError("DataHub topic-sample notification has invalid data.")
    if not isinstance(timestamp, str) or not timestamp:
        raise DataHubWebSocketError("DataHub topic-sample notification has no timestamp.")
    if not isinstance(is_historical, bool):
        raise DataHubWebSocketError("DataHub topic-sample notification has no valid isHistorical flag.")

    return DataHubTopicSample(
        topic=topic,
        channel_id=channel_id,
        data=dict(data),
        timestamp=timestamp,
        is_historical=is_historical,
    )


class DataHubWebSocketClient:
    """Async client for one Axis DataHub topic and channel subscription."""

    _topic_sample_method = "data-hub_v1:topicSample"
    _list_topics_method = "data-hub_v1:listTopics"
    _request_timeout = 10.0

    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        protocol: str,
        topic: str | None,
        channel_id: int | None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        if protocol not in {"https", "http"}:
            raise ValueError("Device API protocol must be http or https.")
        if (topic is None) != (channel_id is None):
            raise ValueError("DataHub topic and channel_id must be provided together.")
        if topic is not None and not topic:
            raise ValueError("DataHub topic is required.")
        if channel_id is not None and channel_id < 1:
            raise ValueError("DataHub channel_id must be a positive integer.")

        self._host = host
        self._username = username
        self._password = password
        self._protocol = protocol
        self._topic = topic
        self._channel_id = channel_id
        self._session_factory = session_factory or self._create_session
        self._session: Any | None = None
        self._websocket: Any | None = None
        self._request_ids = count(1)
        self._pending_samples: deque[DataHubTopicSample] = deque(maxlen=64)

    def _create_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=10),
            middlewares=(_reject_redirects,),
        )

    async def connect(self) -> None:
        """Request a short-lived session token, connect, and subscribe when configured."""
        if self._websocket is not None:
            return

        session = self._session_factory()
        self._session = session
        try:
            if self._protocol == "https":
                logger.warning(
                    "DataHub HTTPS/WSS certificate verification is disabled; use this tool only on trusted networks."
                )
            token_url = f"{self._protocol}://{self._host}/axis-cgi/wssession.cgi"
            session_token = await self._request_session_token(session, token_url)

            if not session_token:
                raise DataHubWebSocketError("DataHub session request returned an empty token.")

            websocket_scheme = "wss" if self._protocol == "https" else "ws"
            query = urlencode({"wssession": session_token, "apis": "data-hub"})
            websocket_url = f"{websocket_scheme}://{self._host}/vapix/ws/v1?{query}"
            self._websocket = await session.ws_connect(
                websocket_url,
                heartbeat=30,
                max_msg_size=0,
                ssl=False,
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=1),
            )
            if self._topic is not None:
                assert self._channel_id is not None
                response = await self._request(
                    method="data-hub_v1:subscribe",
                    params=build_subscribe_params(self._topic, self._channel_id),
                )
                result = response.get("result")
                subscription_id = result.get("subscriptionId") if isinstance(result, dict) else None
                if not isinstance(subscription_id, str) or not subscription_id:
                    raise DataHubWebSocketError("DataHub subscribe response has no subscription ID.")
        except DataHubWebSocketError:
            await self.close()
            raise
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise DataHubWebSocketError(
                f"DataHub WebSocket connection failed for {self._host} ({type(exc).__name__})."
            ) from exc

    async def _request_session_token(self, session: Any, token_url: str) -> str:
        """Request a session token using the device's advertised auth scheme."""
        timeout = aiohttp.ClientTimeout(total=10, sock_read=10)
        async with session.get(
            token_url,
            ssl=False,
            timeout=timeout,
        ) as response:
            if 200 <= response.status < 300:
                return str((await response.text()).strip())
            if response.status != 401:
                raise DataHubWebSocketError(f"DataHub session request failed with HTTP {response.status}.")
            schemes = {
                challenge.split(maxsplit=1)[0].lower()
                for header in response.headers.getall("WWW-Authenticate", [])
                for challenge in parse_http_list(header)
                if challenge.strip()
            }

        auth_options: dict[str, Any]
        if "digest" in schemes:
            auth_options = {
                "middlewares": (_reject_redirects, aiohttp.DigestAuthMiddleware(self._username, self._password))
            }
        elif "basic" in schemes:
            auth_options = {"headers": {"Authorization": aiohttp.encode_basic_auth(self._username, self._password)}}
        else:
            raise DataHubWebSocketError("DataHub session request returned no supported authentication challenge.")

        async with session.get(
            token_url,
            **auth_options,
            ssl=False,
            timeout=timeout,
        ) as response:
            if not 200 <= response.status < 300:
                raise DataHubWebSocketError(f"DataHub session request failed with HTTP {response.status}.")
            return str((await response.text()).strip())

    async def list_topics(self) -> tuple[str, ...]:
        """Return the topic names advertised by the connected DataHub API."""
        response = await self._request(method=self._list_topics_method, params={})
        result = response.get("result")
        topics = result.get("topics") if isinstance(result, dict) else None
        if not isinstance(topics, list):
            raise DataHubWebSocketError("DataHub listTopics response has no topics array.")

        topic_names: list[str] = []
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            name = topic.get("name")
            if isinstance(name, str) and name:
                topic_names.append(name)
        return tuple(topic_names)

    async def receive_sample(self) -> DataHubTopicSample:
        """Wait for the next sample; cancellation stops an idle receiver."""
        if self._pending_samples:
            return self._pending_samples.popleft()

        while True:
            message = await self._receive_json()
            if message.get("method") == self._topic_sample_method:
                return parse_topic_sample(message)
            if "method" not in message:
                raise DataHubWebSocketError("Unexpected DataHub WebSocket response while receiving samples.")

    async def close(self) -> None:
        """Close the WebSocket and HTTP session, tolerating repeated cleanup."""
        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            try:
                await asyncio.wait_for(websocket.close(), timeout=1.0)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.debug(f"DataHub WebSocket close failed: {type(exc).__name__}")

        session = self._session
        self._session = None
        if session is not None:
            try:
                await asyncio.wait_for(session.close(), timeout=1.0)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.debug(f"DataHub HTTP session close failed: {type(exc).__name__}")

        self._pending_samples.clear()

    async def _request(self, *, method: str, params: JsonObject) -> JsonObject:
        websocket = self._websocket
        if websocket is None:
            raise DataHubWebSocketError("DataHub WebSocket is not connected.")

        request_id = f"ax-devil-{next(self._request_ids)}"
        last_notification: str | None = None

        async def exchange() -> JsonObject:
            nonlocal last_notification
            await websocket.send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                message = await self._receive_json()
                if message.get("method") == self._topic_sample_method:
                    self._pending_samples.append(parse_topic_sample(message))
                    continue
                if message.get("id") != request_id:
                    if "method" in message:
                        last_notification = str(message["method"])
                        continue
                    raise DataHubWebSocketError("DataHub response ID did not match the active request.")
                if "error" in message:
                    error = message["error"]
                    code = error.get("code") if isinstance(error, dict) else "unknown"
                    raise DataHubWebSocketError(f"DataHub request {method} failed with error code {code}.")
                if "result" not in message:
                    raise DataHubWebSocketError(f"DataHub response to {method} has no result.")
                return message

        try:
            return await asyncio.wait_for(exchange(), timeout=self._request_timeout)
        except asyncio.TimeoutError as exc:
            message = f"Timed out waiting for DataHub response to {method}."
            if last_notification is not None:
                message = f"{message} Last server notification: {last_notification}."
            raise DataHubWebSocketError(message) from exc

    async def _receive_json(self) -> JsonObject:
        if self._websocket is None:
            raise DataHubWebSocketError("DataHub WebSocket is not connected.")

        while True:
            try:
                message = await self._websocket.receive()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise DataHubWebSocketError("DataHub WebSocket transport error.") from exc

            if message.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise DataHubWebSocketError("DataHub WebSocket returned invalid JSON.") from exc
                if not isinstance(payload, dict):
                    raise DataHubWebSocketError("DataHub WebSocket returned a non-object JSON message.")
                return payload
            if message.type in {WSMsgType.PING, WSMsgType.PONG}:
                continue
            if message.type == WSMsgType.ERROR:
                raise DataHubWebSocketError("DataHub WebSocket transport error.")
            raise DataHubWebSocketError("DataHub WebSocket closed before a JSON message was received.")


async def discover_datahub_topics(
    *,
    host: str,
    username: str,
    password: str,
    protocol: str,
    session_factory: SessionFactory | None = None,
) -> tuple[str, ...]:
    """Open a discovery-only DataHub session and return its available topics."""
    client = DataHubWebSocketClient(
        host=host,
        username=username,
        password=password,
        protocol=protocol,
        topic=None,
        channel_id=None,
        session_factory=session_factory,
    )
    try:
        await client.connect()
        return await client.list_topics()
    finally:
        await client.close()
