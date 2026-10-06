"""Exercise browser relay lifecycle and local-origin restrictions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest
from server import Bridge, Viewer, create_app
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect


class _ReplySocket:
    """Reply at the external socket boundary using the selected viewer's pending request."""

    def __init__(self, bridge: Bridge, session_id: str) -> None:
        self.bridge = bridge
        self.session_id = session_id
        self.requests: list[dict[str, Any]] = []

    async def send_json(self, request: dict[str, Any]) -> None:
        """Resolve the request immediately so incorrect routing fails without a timeout."""
        self.requests.append(request)
        self.bridge.viewers[self.session_id].pending[request["id"]].set_result(
            {"recording_id": f"{self.session_id}-recording"}
        )


def test_explicit_session_routes_to_the_selected_viewer() -> None:
    """A second tab can be selected and a disconnected identity cannot select another tab."""
    bridge = Bridge()
    first, second = (_ReplySocket(bridge, identity) for identity in ("first", "second"))
    bridge.viewers = {"first": Viewer(cast(WebSocket, first)), "second": Viewer(cast(WebSocket, second))}

    async def exercise() -> None:
        result = await bridge.request("get_view", {"limit": 5}, "second")
        assert result == {"session_id": "second", "recording_id": "second-recording"}
        assert first.requests == []
        assert len(second.requests) == 1
        assert second.requests[0]["method"] == "get_view"
        assert second.requests[0]["args"] == {"limit": 5}
        assert bridge.viewers["second"].pending == {}
        with pytest.raises(ValueError, match="disconnected"):
            await bridge.request("get_view", {}, "missing")
        assert len(second.requests) == 1

    asyncio.run(exercise())


def test_empty_bridge_reports_how_to_connect() -> None:
    """An agent gets actionable discovery information before a browser connects."""
    result = asyncio.run(Bridge().request("get_view", {}, None))
    assert result["status"] == "no_viewer"


def test_viewer_relay_and_disconnect() -> None:
    """A request reaches the chosen tab, and its reply returns through the bridge."""
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        portal = client.portal
        assert portal is not None
        with client.websocket_connect(
            "ws://127.0.0.1:8766/bridge", headers={"origin": "http://127.0.0.1:8766"}
        ) as socket:
            with ThreadPoolExecutor() as executor:
                response = executor.submit(portal.call, app.state.bridge.request, "get_view", {}, None)
                request = socket.receive_json()
                assert request["method"] == "get_view"
                socket.send_json({"id": request["id"], "result": {"status": "ready", "recording_id": "example"}})
                assert response.result(timeout=5)["recording_id"] == "example"
        result = portal.call(app.state.bridge.request, "get_view", {}, None)
        assert result["status"] == "no_viewer"


def test_multiple_tabs_require_explicit_session() -> None:
    """The server never guesses which of two open viewers the human means."""
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        portal = client.portal
        assert portal is not None
        headers = {"origin": "http://127.0.0.1:8766"}
        with client.websocket_connect("ws://127.0.0.1:8766/bridge", headers=headers):
            with client.websocket_connect("ws://127.0.0.1:8766/bridge", headers=headers):
                result = portal.call(app.state.bridge.request, "get_view", {}, None)
                assert result["status"] == "choose_viewer"
                assert len(result["sessions"]) == 2


def test_foreign_origins_and_hosts_are_rejected() -> None:
    """An unrelated website cannot use the browser relay or MCP endpoint."""
    with TestClient(create_app(), base_url="http://127.0.0.1:8766") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://127.0.0.1:8766/bridge", headers={"origin": "https://unrelated.example"}
            ):
                pytest.fail("foreign origin accepted")
        assert client.get("/", headers={"host": "unrelated.example"}).status_code == 400
        assert client.post("/mcp", json={}, headers={"origin": "https://unrelated.example"}).status_code == 403
        assert client.get("/server.py").status_code == 404


def test_disconnect_fails_pending_request() -> None:
    """Closing the browser reports disconnection rather than returning stale data."""
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        portal = client.portal
        assert portal is not None
        with client.websocket_connect(
            "ws://127.0.0.1:8766/bridge", headers={"origin": "http://127.0.0.1:8766"}
        ) as socket:
            with ThreadPoolExecutor() as executor:
                response = executor.submit(portal.call, app.state.bridge.request, "get_view", {}, None)
                socket.receive_json()
                socket.close()
                with pytest.raises(ValueError, match="disconnected"):
                    response.result(timeout=5)


def rpc(client: TestClient, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Exercise the public MCP HTTP transport, including SDK validation and serialization."""
    response = client.post(
        "/mcp",
        headers={"accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert "error" not in body, body
    result: dict[str, Any] = body["result"]
    return result


def test_mcp_discovery_teaches_the_workflow() -> None:
    """Agents discover instructions, precise input/output schemas, and the guide over MCP."""
    with TestClient(create_app(), base_url="http://127.0.0.1:8766") as client:
        initialized = rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert "Start with get_view" in initialized["instructions"]
        assert "trace://guide" in initialized["instructions"]
        tools = {tool["name"]: tool for tool in rpc(client, "tools/list", {})["tools"]}
        assert set(tools) == {"get_view", "search_flame", "search_timeline", "inspect", "focus"}
        for name, tool in tools.items():
            for parameter in tool["inputSchema"]["properties"].values():
                assert parameter.get("description"), (name, parameter)
            assert tool["outputSchema"]["properties"]["recording_id"]["description"]
            assert tool["annotations"]["readOnlyHint"] == (name != "focus")
        flame = tools["search_flame"]["inputSchema"]["properties"]
        assert "start_ms" not in flame and "end_ms" not in flame and "view" not in flame
        assert flame["sort"]["enum"] == ["total", "self"]
        assert flame["limit"]["default"] == 5
        timeline = tools["search_timeline"]["inputSchema"]["properties"]
        assert "start_ms" in timeline and "start" in timeline["sort"]["enum"]
        resources = rpc(client, "resources/list", {})["resources"]
        assert any(resource["uri"] == "trace://guide" for resource in resources)
        guide = rpc(client, "resources/read", {"uri": "trace://guide"})["contents"][0]
        assert guide["mimeType"] == "text/markdown"
        assert guide["text"] == (Path(__file__).parent / "AGENT_GUIDE.md").read_text()
        for name, arguments in [("get_view", {}), ("search_flame", {"recording_id": "r"})]:
            result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
            assert not result.get("isError")
            assert result["structuredContent"]["status"] == "no_viewer"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("search_flame", {"recording_id": "r", "sort": "start"}),
        ("search_timeline", {"recording_id": "r", "start_ms": 2, "end_ms": 1}),
        ("search_timeline", {"recording_id": "r", "limit": 51}),
        ("focus", {"recording_id": "r", "item_id": "i", "start_ms": 0, "end_ms": 1}),
    ],
)
def test_mcp_rejects_invalid_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Invalid sorting, intervals, paging, and ambiguous navigation return tool errors."""
    with TestClient(create_app(), base_url="http://127.0.0.1:8766") as client:
        result = rpc(client, "tools/call", {"name": name, "arguments": arguments})
        assert result["isError"]


@pytest.mark.parametrize("name,view", [("search_flame", "flame"), ("search_timeline", "timeline")])
def test_mcp_search_relays_and_preserves_typed_results(name: str, view: str) -> None:
    """Public search tools translate to the existing browser operation without losing result fields."""
    item: dict[str, Any] = {
        "item_id": "flame:1" if view == "flame" else "timeline:0:0",
        "name": "worker",
        "thread_id": "1:2",
        "total_ms": 10,
        "self_ms": 5,
    }
    if view == "flame":
        item.update(
            count=3,
            count_kind="sightings",
            thread_share=0.5,
            parent_id="flame:0",
            child_count=1,
            representative_id="timeline:0:0",
        )
    else:
        item.update(start_ms=0, end_ms=10, depth=1, timing="sample_window")
    with TestClient(create_app(), base_url="http://127.0.0.1:8766") as client:
        with client.websocket_connect(
            "ws://127.0.0.1:8766/bridge", headers={"origin": "http://127.0.0.1:8766"}
        ) as socket:
            with ThreadPoolExecutor() as executor:
                response = executor.submit(
                    rpc,
                    client,
                    "tools/call",
                    {
                        "name": name,
                        "arguments": {"recording_id": "r", "query": "worker", "thread_id": "1:2"},
                    },
                )
                request = socket.receive_json()
                assert request["method"] == "search"
                assert request["args"]["view"] == view
                assert request["args"]["thread_id"] == "1:2"
                assert request["args"]["limit"] == 5
                socket.send_json(
                    {
                        "id": request["id"],
                        "result": {
                            "recording_id": "r",
                            "view": view,
                            "scope": "whole_recording" if view == "flame" else "overlapping",
                            "matched": 1,
                            "offset": 0,
                            "items": [item],
                            "next_offset": None,
                            "refine_search": False,
                            "timing": "sampled_estimates_including_waits",
                        },
                    }
                )
                result = response.result(timeout=5)
                assert not result.get("isError"), result
                returned = result["structuredContent"]
                assert returned["session_id"]
                assert returned["recording_id"] == "r"
                assert returned["next_offset"] is None
                assert returned["items"][0].items() >= item.items()
