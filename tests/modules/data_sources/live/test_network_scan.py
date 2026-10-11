"""Tests for the SSDP network scan that finds Axis cameras."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ax_devil.modules.data_sources.live.network_scan import (
    DiscoveredCamera,
    NetworkScanTransport,
    SsdpResponse,
    scan_network,
)


def _description(manufacturer: str = "AXIS", model: str = "AXIS P3265-LVE", serial: str = "ACCC8E000001") -> str:
    return (
        '<?xml version="1.0"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
        f"<friendlyName>{model} - {serial}</friendlyName>"
        f"<manufacturer>{manufacturer}</manufacturer>"
        f"<modelName>{model}</modelName>"
        f"<serialNumber>{serial}</serialNumber>"
        "</device></root>"
    )


class _FakeTransport:
    """Answers a search with fixed responses and serves descriptions from a dict keyed by URL."""

    def __init__(self, responses: list[SsdpResponse], pages: dict[str, str], search_error: OSError | None = None):
        self._responses = responses
        self._pages = pages
        self._search_error = search_error
        self.fetched: list[str] = []

    def search(self, timeout: float) -> list[SsdpResponse]:
        if self._search_error is not None:
            raise self._search_error
        return self._responses

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        if url not in self._pages:
            raise OSError("unreachable")
        return self._pages[url]


def _scan(pages_by_host: dict[str, str]) -> list[DiscoveredCamera]:
    """Scan a network where each host answers with a description served at its own URL."""
    urls = {host: f"http://{host}/desc.xml" for host in pages_by_host}
    responses = [SsdpResponse(host, url) for host, url in urls.items()]
    pages = {urls[host]: page for host, page in pages_by_host.items()}
    return scan_network(transport=_FakeTransport(responses, pages))


def test_axis_device_is_kept_with_model_and_serial() -> None:
    assert _scan({"192.168.0.90": _description()}) == [
        DiscoveredCamera("192.168.0.90", "AXIS P3265-LVE", "ACCC8E000001")
    ]


def test_manufacturer_match_ignores_case_and_surrounding_text() -> None:
    cameras = _scan({"192.168.0.90": _description(manufacturer="Axis Communications AB")})

    assert [camera.host for camera in cameras] == ["192.168.0.90"]


def test_non_axis_device_is_dropped() -> None:
    assert _scan({"192.168.0.10": _description(manufacturer="Netgear"), "192.168.0.90": _description()}) == [
        DiscoveredCamera("192.168.0.90", "AXIS P3265-LVE", "ACCC8E000001")
    ]


def test_model_falls_back_to_friendly_name_without_serial_suffix() -> None:
    document = (
        "<root><device><friendlyName>AXIS M1065-L - ACCC8E000002</friendlyName>"
        "<manufacturer>AXIS</manufacturer><serialNumber>ACCC8E000002</serialNumber></device></root>"
    )

    assert _scan({"192.168.0.91": document}) == [DiscoveredCamera("192.168.0.91", "AXIS M1065-L", "ACCC8E000002")]


def test_missing_serial_is_empty() -> None:
    document = "<root><device><manufacturer>AXIS</manufacturer><modelName>AXIS Q1656</modelName></device></root>"

    assert _scan({"192.168.0.92": document}) == [DiscoveredCamera("192.168.0.92", "AXIS Q1656", "")]


def test_device_answering_repeatedly_is_reported_once() -> None:
    url = "http://192.168.0.90/desc.xml"
    responses = [SsdpResponse("192.168.0.90", url)] * 3
    transport = _FakeTransport(responses, {url: _description()})

    assert len(scan_network(transport=transport)) == 1
    assert transport.fetched == [url]


def test_cameras_are_sorted_by_numeric_ip_address() -> None:
    hosts = ["192.168.0.100", "192.168.0.9", "10.0.0.200"]

    assert [camera.host for camera in _scan({host: _description() for host in hosts})] == [
        "10.0.0.200",
        "192.168.0.9",
        "192.168.0.100",
    ]


def test_malformed_description_is_ignored_without_failing_the_scan() -> None:
    cameras = _scan({"192.168.0.10": "<root><device>", "192.168.0.11": "not xml", "192.168.0.90": _description()})

    assert [camera.host for camera in cameras] == ["192.168.0.90"]


def test_unreachable_description_is_ignored() -> None:
    good = "http://192.168.0.90/desc.xml"
    responses = [SsdpResponse("192.168.0.10", "http://192.168.0.10/desc.xml"), SsdpResponse("192.168.0.90", good)]

    cameras = scan_network(transport=_FakeTransport(responses, {good: _description()}))

    assert [camera.host for camera in cameras] == ["192.168.0.90"]


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE root [<!ENTITY a "AXIS">]>',
        "<!doctype root>",
        '<!ENTITY a "AXIS">',
    ],
)
def test_description_with_doctype_or_entity_is_rejected(declaration: str) -> None:
    document = _description().replace("<root", f"{declaration}<root", 1)

    assert _scan({"192.168.0.90": document}) == []


@pytest.mark.parametrize(
    "location",
    [
        "http://192.168.0.200/desc.xml",
        "http://evil.example/desc.xml",
        "http://192.168.0.90@192.168.0.200/desc.xml",
        "http://admin:pw@192.168.0.90/desc.xml",
        "http://192.168.0.90:notaport/desc.xml",
        "https://192.168.0.90/desc.xml",
        "file:///etc/passwd",
        "ftp://192.168.0.90/desc.xml",
        "",
    ],
)
def test_location_not_on_the_responding_address_over_http_is_never_fetched(location: str) -> None:
    transport = _FakeTransport([SsdpResponse("192.168.0.90", location)], {location: _description()})

    assert scan_network(transport=transport) == []
    assert transport.fetched == []


def test_location_with_port_on_responding_address_is_followed() -> None:
    url = "http://192.168.0.90:49152/rootDesc.xml"
    transport = _FakeTransport([SsdpResponse("192.168.0.90", url)], {url: _description()})

    assert [camera.host for camera in scan_network(transport=transport)] == ["192.168.0.90"]


def test_bad_location_does_not_hide_a_later_valid_one_from_the_same_host() -> None:
    good = "http://192.168.0.90/desc.xml"
    responses = [SsdpResponse("192.168.0.90", "http://192.168.0.200/x.xml"), SsdpResponse("192.168.0.90", good)]

    assert len(scan_network(transport=_FakeTransport(responses, {good: _description()}))) == 1


@pytest.mark.parametrize("error", [OSError("Network is unreachable"), PermissionError("denied")])
def test_search_failure_returns_empty_list(error: OSError) -> None:
    assert scan_network(transport=_FakeTransport([], {}, search_error=error)) == []


@pytest.fixture
def http_server() -> Iterator[tuple[str, dict[str, bytes]]]:
    """A loopback HTTP server serving `pages`; any path under /redirect sends the client to /desc."""
    pages: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/desc")
                self.end_headers()
                return
            body = pages.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", pages
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_fetch_returns_a_description_up_to_the_size_limit(http_server: tuple[str, dict[str, bytes]]) -> None:
    base, pages = http_server
    document = _description() + " " * (64 * 1024 - len(_description()))
    pages["/desc"] = document.encode()

    assert NetworkScanTransport().fetch(f"{base}/desc") == document


def test_fetch_rejects_a_description_over_the_size_limit(http_server: tuple[str, dict[str, bytes]]) -> None:
    base, pages = http_server
    pages["/desc"] = (_description() + " " * (64 * 1024)).encode()

    with pytest.raises(ValueError):
        NetworkScanTransport().fetch(f"{base}/desc")


def test_fetch_does_not_follow_redirects(http_server: tuple[str, dict[str, bytes]]) -> None:
    base, pages = http_server
    pages["/desc"] = _description().encode()

    with pytest.raises(OSError):
        NetworkScanTransport().fetch(f"{base}/redirect")


class _FakeUdpSocket:
    """Stands in for the UDP socket: records what is sent and replays canned datagrams, then times out."""

    def __init__(self, datagrams: list[tuple[bytes, tuple[str, int]]]) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self._datagrams = datagrams

    def __enter__(self) -> _FakeUdpSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def setsockopt(self, *args: object) -> None:
        return None

    def settimeout(self, value: float | None) -> None:
        return None

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        self.sent.append((data, address))
        return len(data)

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        if not self._datagrams:
            raise TimeoutError
        return self._datagrams.pop(0)


def test_search_sends_one_ssdp_discovery_and_collects_successful_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nLocation: http://192.168.0.90:49152/d.xml\r\n\r\n"
    notify = b"NOTIFY * HTTP/1.1\r\nLOCATION: http://192.168.0.91/d.xml\r\n\r\n"
    not_found = b"HTTP/1.1 404 Not Found\r\nLOCATION: http://192.168.0.92/d.xml\r\n\r\n"
    fake = _FakeUdpSocket(
        [(answer, ("192.168.0.90", 1900)), (notify, ("192.168.0.91", 1900)), (not_found, ("192.168.0.92", 1900))]
    )
    monkeypatch.setattr(socket, "socket", lambda *args: fake)

    responses = NetworkScanTransport().search(timeout=5.0)

    assert responses == [SsdpResponse("192.168.0.90", "http://192.168.0.90:49152/d.xml")]
    [(request, destination)] = fake.sent
    assert destination == ("239.255.255.250", 1900)
    assert request.startswith(b"M-SEARCH * HTTP/1.1\r\n")
    assert b'MAN: "ssdp:discover"\r\n' in request
    assert b"ST: upnp:rootdevice\r\n" in request
    assert b"MX: 2\r\n" in request
