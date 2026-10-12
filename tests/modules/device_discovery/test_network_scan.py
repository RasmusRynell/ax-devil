"""The network scan finds Axis devices by what they give without login, and signs in only with the logins it is given,
in order, never sending a plain password."""

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
    Login,
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

    Signed-in requests are challenged for HTTP Digest; a Digest answer for the user *accepts* is accepted, after the
    first *turned_away* answers are turned away with HTTP 429, as a device limiting sign-in attempts does.
    """

    def __init__(self, axis: bool, accepts: str | None, turned_away: int, properties: dict[str, str]) -> None:
        self.authorizations: list[str] = []
        device = self
        turn_away = [turned_away]

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
                elif authorization.startswith("Digest ") and turn_away[0] > 0:
                    turn_away[0] -= 1
                    self._answer(429, {})
                elif accepts is not None and authorization.startswith(f'Digest username="{accepts}"'):
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
    monkeypatch.setattr(network_scan, "_RATE_LIMIT_RETRY_SECONDS", (0.0, 0.0))

    def start(
        *, axis: bool, accepts: str | None = "root", turned_away: int = 0, properties: dict[str, str] = _UNRESTRICTED
    ) -> _Device:
        device = _Device(axis, accepts, turned_away, properties)
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
    root = Login("root", "secret")

    [camera] = scan_network(targets=["127.0.0.1"], logins=[root])

    assert (camera.sign_in, camera.login) == (SignIn.SIGNED_IN, root)
    assert (camera.architecture, camera.soc) == ("aarch64", "Ambarella CV25")
    assert device.authorizations
    assert all(header.startswith("Digest ") and "secret" not in header for header in device.authorizations)


def test_logins_are_tried_in_order_until_one_signs_in(
    serve: Callable[..., _Device], monkeypatch: pytest.MonkeyPatch
) -> None:
    device = serve(axis=True, accepts="operator")
    monkeypatch.setenv("AX_DEVIL_TEST_SCAN_USER", "operator")
    operator = Login("$AX_DEVIL_TEST_SCAN_USER", "secret")

    [camera] = scan_network(targets=["127.0.0.1"], logins=[Login("guest", "guest"), operator, Login("root", "x")])

    assert (camera.sign_in, camera.login) == (SignIn.SIGNED_IN, operator), "the login is kept as written"
    assert not any('username="root"' in header for header in device.authorizations), (
        "no login after the one that worked"
    )


def test_a_device_that_turns_sign_ins_away_for_now_is_asked_again(serve: Callable[..., _Device]) -> None:
    serve(axis=True, turned_away=2)

    [camera] = scan_network(targets=["127.0.0.1"], logins=[Login("root", "secret")])

    assert camera.sign_in is SignIn.SIGNED_IN


def test_refused_credentials_still_list_the_device(serve: Callable[..., _Device]) -> None:
    serve(axis=True, accepts=None)

    [camera] = scan_network(targets=["127.0.0.1"], logins=[Login("root", "wrong"), Login("admin", "wrong")])

    assert (camera.model, camera.sign_in, camera.login, camera.architecture) == (
        "AXIS P3265-LVE",
        SignIn.REFUSED,
        None,
        "",
    )


def test_a_login_whose_username_is_an_unset_variable_is_never_sent(
    serve: Callable[..., _Device], monkeypatch: pytest.MonkeyPatch
) -> None:
    device = serve(axis=True)
    monkeypatch.delenv("AX_DEVIL_TEST_UNSET_USER", raising=False)

    [camera] = scan_network(targets=["127.0.0.1"], logins=[Login("$AX_DEVIL_TEST_UNSET_USER", "x")])

    assert camera.sign_in is SignIn.NOT_TRIED
    assert device.authorizations == []


def test_no_login_is_sent_once_the_scan_is_cancelled(serve: Callable[..., _Device]) -> None:
    device = serve(axis=True)
    cancelled = threading.Event()
    cancelled.set()

    camera = network_scan._signed_in(
        DiscoveredCamera("127.0.0.1", "m", "s"),
        lambda user, password: DeviceConfig.http("127.0.0.1", user, password, port=device.port),
        [Login("root", "secret")],
        cancelled,
    )

    assert camera.sign_in is SignIn.NOT_TRIED
    assert device.authorizations == []


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

    assert scan_network(targets=["127.0.0.1"], logins=[Login("root", "secret")]) == []
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

    camera = network_scan._signed_in(
        DiscoveredCamera("127.0.0.1", "", "", ""),
        lambda user, password: DeviceConfig.https("127.0.0.1", user, password),
        [Login("root", "secret")],
        threading.Event(),
    )

    assert camera.sign_in is SignIn.SIGNED_IN
    assert methods == [AuthMethod.DIGEST]


def test_credentials_go_only_to_identified_axis_devices_and_only_when_given(serve: Callable[..., _Device]) -> None:
    other, axis = serve(axis=False), serve(axis=True)

    scan_network(targets=["127.0.0.1"])
    assert axis.authorizations == []

    scan_network(targets=["127.0.0.1"], logins=[Login("root", "secret")])
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
