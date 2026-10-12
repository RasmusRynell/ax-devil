"""The network scan finds Axis devices by what they give without login, and signs in only when asked, never sending a
plain password over HTTP."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from ax_devil_device_api import DeviceConfig
from ax_devil_device_api.core.config import AuthMethod

from ax_devil.modules.device_discovery import network_scan
from ax_devil.modules.device_discovery.network_scan import (
    DeviceOrder,
    DiscoveredCamera,
    SignIn,
    parse_scan_targets,
    scan_network,
)

_UNRESTRICTED = {
    "Brand": "AXIS",
    "ProdFullName": "AXIS P3265-LVE Dome Camera",
    "ProdShortName": "AXIS P3265-LVE",
    "ProdType": "Dome Camera",
    "SerialNumber": "ACCC8E000001",
    "Version": "12.6.85",
}
_ALL = {**_UNRESTRICTED, "Architecture": "aarch64", "Soc": "Ambarella CV25"}


class _Device:
    """A loopback web server that answers as an Axis device would, with *properties* unrestricted, or 404 to
    everything if *axis* is false.

    Signed-in requests are challenged for HTTP Digest; any Digest answer is accepted when *accepts* is true.
    """

    def __init__(self, axis: bool, accepts: bool, properties: dict[str, str]) -> None:
        self.authorizations: list[str] = []
        device = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                authorization = self.headers.get("Authorization", "")
                if authorization:
                    device.authorizations.append(authorization)
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if not axis:
                    self._answer(404, {})
                elif self.path == "/axis-cgi/systemready.cgi":
                    self._answer(200, {"data": {"systemready": "yes", "uptime": "93600"}})
                elif request.get("method") == "getAllUnrestrictedProperties":
                    self._answer(200, {"data": {"propertyList": properties}})
                elif accepts and authorization.startswith("Digest "):
                    self._answer(200, {"data": {"propertyList": _ALL}})
                else:
                    self._answer(401, {}, ("WWW-Authenticate", 'Digest realm="AXIS", nonce="n0", qop="auth"'))

            def _answer(self, status: int, body: dict[str, Any], *header: tuple[str, str]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                for name, value in header:
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., _Device]]:
    """Start loopback devices and make them the only ports the scan probes, in the order started."""
    devices: list[_Device] = []
    ports: dict[int, Callable[[str, str, str], object]] = {}
    monkeypatch.setattr(network_scan, "_PORT_CONFIGS", ports)

    def start(*, axis: bool, accepts: bool = True, properties: dict[str, str] = _UNRESTRICTED) -> _Device:
        device = _Device(axis, accepts, properties)
        devices.append(device)
        ports[device.port] = partial(DeviceConfig.http, port=device.port)
        return device

    yield start
    for device in devices:
        device.close()


def test_an_axis_device_is_found_with_what_it_gives_without_login(serve: Callable[..., _Device]) -> None:
    serve(axis=True)
    found: list[DiscoveredCamera] = []

    cameras = scan_network(targets=["127.0.0.1"], found=found.append)

    assert cameras == [
        DiscoveredCamera("127.0.0.1", "AXIS P3265-LVE", "ACCC8E000001", "12.6.85", "Dome Camera", 93600.0)
    ]
    assert found == cameras, "each device is reported as soon as it is identified"
    assert cameras[0].uptime == "1d 2h"


def test_signing_in_reads_more_and_sends_only_a_digest_answer(serve: Callable[..., _Device]) -> None:
    device = serve(axis=True)

    [camera] = scan_network(targets=["127.0.0.1"], username="root", password="secret")

    assert (camera.sign_in, camera.architecture, camera.soc) == (SignIn.SIGNED_IN, "aarch64", "Ambarella CV25")
    assert device.authorizations
    assert all(header.startswith("Digest ") and "secret" not in header for header in device.authorizations)


def test_refused_credentials_still_list_the_device(serve: Callable[..., _Device]) -> None:
    serve(axis=True, accepts=False)

    [camera] = scan_network(targets=["127.0.0.1"], username="root", password="wrong")

    assert (camera.model, camera.sign_in, camera.architecture) == ("AXIS P3265-LVE", SignIn.REFUSED, "")


def test_another_web_server_is_not_listed_and_does_not_hide_the_axis_one(serve: Callable[..., _Device]) -> None:
    serve(axis=False)
    assert scan_network(targets=["127.0.0.1"]) == []

    serve(axis=True)
    assert [camera.model for camera in scan_network(targets=["127.0.0.1"])] == ["AXIS P3265-LVE"]


@pytest.mark.parametrize(
    "properties", [{k: v for k, v in _UNRESTRICTED.items() if k != "Brand"}, {**_UNRESTRICTED, "Brand": "Other"}]
)
def test_a_device_that_does_not_say_it_is_axis_is_not_listed_or_signed_in_to(
    serve: Callable[..., _Device], properties: dict[str, str]
) -> None:
    device = serve(axis=True, properties=properties)

    assert scan_network(targets=["127.0.0.1"], username="root", password="secret") == []
    assert device.authorizations == []


def test_signing_in_over_https_asks_for_digest_not_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    methods: list[AuthMethod] = []

    class _Client:
        def __init__(self, config: DeviceConfig) -> None:
            methods.append(config.auth_method)
            self.device = self

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get_info_auth(self) -> dict[str, str]:
            return _ALL

    monkeypatch.setattr(network_scan, "Client", _Client)
    config = DeviceConfig.https("127.0.0.1", "root", "secret")
    config.auth_method = AuthMethod.AUTO

    camera = network_scan._signed_in(DiscoveredCamera("127.0.0.1", "", "", ""), config)

    assert camera.sign_in is SignIn.SIGNED_IN
    assert methods == [AuthMethod.DIGEST]


def test_signing_in_goes_through_the_endpoint_that_identified_the_device(
    serve: Callable[..., _Device], monkeypatch: pytest.MonkeyPatch
) -> None:
    axis, broken = serve(axis=True), serve(axis=False)
    ports = network_scan._PORT_CONFIGS
    monkeypatch.setitem(ports, broken.port, partial(DeviceConfig.https, port=broken.port))  # TLS to a plain server

    [camera] = scan_network(targets=["127.0.0.1"], username="root", password="secret")

    assert camera.sign_in is SignIn.SIGNED_IN
    assert axis.authorizations


def test_credentials_go_only_to_identified_axis_devices_and_only_when_given(serve: Callable[..., _Device]) -> None:
    other, axis = serve(axis=False), serve(axis=True)

    scan_network(targets=["127.0.0.1"])
    assert axis.authorizations == []

    scan_network(targets=["127.0.0.1"], username="root", password="secret")
    assert other.authorizations == []
    assert axis.authorizations


def test_orders_compare_firmware_by_number_and_list_unknown_values_last() -> None:
    cameras = [
        DiscoveredCamera("10.0.0.3", "AXIS Q1656", "", "12.10.1", uptime_seconds=50.0),
        DiscoveredCamera("10.0.0.1", "", "", ""),
        DiscoveredCamera("10.0.0.2", "AXIS M3086-V", "", "12.9.4", uptime_seconds=10.0),
    ]

    def hosts(order: DeviceOrder) -> list[str]:
        return [camera.host for camera in order.sorted(cameras)]

    assert hosts(DeviceOrder.ADDRESS) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert hosts(DeviceOrder.MODEL) == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]
    assert hosts(DeviceOrder.FIRMWARE) == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]
    assert hosts(DeviceOrder.UPTIME) == ["10.0.0.2", "10.0.0.3", "10.0.0.1"]


def test_scan_targets_are_addresses_and_ranges_without_repeats() -> None:
    assert parse_scan_targets("") == ()
    assert parse_scan_targets("10.0.0.5, 10.0.0.0/30 10.0.0.5") == ("10.0.0.1", "10.0.0.2", "10.0.0.5")
    assert parse_scan_targets("10.0.0.7/32") == ("10.0.0.7",)
    assert len(parse_scan_targets("192.168.1.0/24")) == 254


def test_ranges_are_counted_before_they_are_expanded() -> None:
    assert len(parse_scan_targets(" ".join(["10.0.0.0/20"] * 100_000))) == 4094
    with pytest.raises(ValueError, match="at most"):
        parse_scan_targets("10.0.0.0/20 10.1.0.0/24")


def test_overlapping_ranges_count_their_addresses_once() -> None:
    assert len(parse_scan_targets("10.0.0.0/20 10.0.0.0/21 10.0.0.9")) == 4094
    assert parse_scan_targets("10.0.0.0/24 10.0.0.0") == tuple(f"10.0.0.{i}" for i in range(0, 255)), (
        "an address named on its own is scanned even where a range leaves it out"
    )


@pytest.mark.parametrize("text", ["camera", "10.0.0.256", "10.0.0.0/33", "fe80::1", "10.0.0.0/8"])
def test_scan_targets_reject_what_is_not_a_small_ipv4_range(text: str) -> None:
    with pytest.raises(ValueError):
        parse_scan_targets(text)
