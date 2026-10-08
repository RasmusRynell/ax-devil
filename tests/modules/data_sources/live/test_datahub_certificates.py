"""Exercise unverified HTTPS/WSS transport and redirect rejection."""

from __future__ import annotations

import asyncio
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ax_devil.modules.data_sources.live.datahub_client import (
    DataHubWebSocketClient,
    DataHubWebSocketError,
)


@pytest.fixture(scope="module")
def certificate_files(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Reuse immutable certificate bytes while each test owns its TLS endpoints."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("Local TLS regression requires openssl to generate a disposable certificate")
    directory = tmp_path_factory.mktemp("tls")
    certificate = directory / "certificate.pem"
    private_key = directory / "key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    return certificate, private_key


def test_unverified_https_and_wss_connections(certificate_files: tuple[Path, Path]) -> None:
    """Use encrypted HTTPS/WSS transport without requiring a trusted device certificate."""
    certificate, private_key = certificate_files
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate, private_key)
    requests: list[str] = []

    async def token(request: web.Request) -> web.Response:
        assert request.secure
        requests.append(request.headers.get("Authorization", ""))
        if not requests[-1]:
            return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="test"'})
        return web.Response(text="test-token")

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        assert request.secure
        assert request.query["wssession"] == "test-token"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        return ws

    async def check_connections() -> None:
        app = web.Application()
        app.router.add_get("/axis-cgi/wssession.cgi", token)
        app.router.add_get("/vapix/ws/v1", websocket)
        server = TestServer(app, scheme="https")
        await server.start_server(ssl=server_context)
        try:
            host = f"127.0.0.1:{server.port}"
            client = DataHubWebSocketClient(
                host=host,
                username="user",
                password="pass",
                protocol="https",
                topic=None,
                channel_id=None,
            )
            await client.connect()
            assert client._websocket is not None
            await client.close()
            assert requests == ["", "Basic dXNlcjpwYXNz"]
        finally:
            await server.close()

    asyncio.run(check_connections())


@pytest.mark.parametrize("stage", ["token", "basic", "digest", "websocket"])
@pytest.mark.parametrize("status", [302, 307])
def test_redirects_are_rejected_without_following(
    certificate_files: tuple[Path, Path], stage: str, status: int
) -> None:
    """Redirects are rejected before another host or protocol can be reached."""
    certificate, private_key = certificate_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    target_requests: list[str] = []
    source_requests: list[str] = []

    async def target_handler(request: web.Request) -> web.Response:
        target_requests.append(request.path)
        return web.Response(text="redirected-token")

    async def exercise() -> None:
        target_app = web.Application()
        target_app.router.add_get("/redirected", target_handler)
        target = TestServer(target_app)
        await target.start_server()
        try:
            location = f"http://localhost:{target.port}/redirected"

            async def source_handler(request: web.Request) -> web.Response:
                authorization = request.headers.get("Authorization", "")
                source_requests.append(authorization)
                if stage == "websocket" and request.path == "/axis-cgi/wssession.cgi":
                    return web.Response(text="test-token")
                if stage in {"basic", "digest"} and not authorization:
                    challenge = (
                        'Basic realm="test"'
                        if stage == "basic"
                        else ('Digest realm="test", nonce="test-nonce", algorithm=SHA-256, qop="auth"')
                    )
                    return web.Response(status=401, headers={"WWW-Authenticate": challenge})
                return web.Response(status=status, headers={"Location": location})

            source_app = web.Application()
            source_app.router.add_get("/axis-cgi/wssession.cgi", source_handler)
            source_app.router.add_get("/vapix/ws/v1", source_handler)
            source = TestServer(source_app, scheme="https")
            await source.start_server(ssl=context)
            try:
                client = DataHubWebSocketClient(
                    host=f"127.0.0.1:{source.port}",
                    username="user",
                    password="pass",
                    protocol="https",
                    topic=None,
                    channel_id=None,
                )
                with pytest.raises(DataHubWebSocketError, match="redirects are not allowed") as error:
                    await client.connect()
                assert type(error.value) is DataHubWebSocketError
                assert target_requests == []
                assert client._session is None
                assert client._websocket is None
                if stage in {"basic", "digest"}:
                    assert source_requests[-1].lower().startswith(f"{stage} ")
                else:
                    assert len(source_requests) == (2 if stage == "websocket" else 1)
            finally:
                await source.close()
        finally:
            await target.close()

    asyncio.run(exercise())
