"""Find Axis devices on IPv4 addresses: a TCP probe for the web ports, then Axis's device information on each open one.

A scan identifies devices from what they give without login: the unrestricted ``basicdeviceinfo.cgi`` properties and
``systemready.cgi``. Only when given logins does it send credentials, and only to devices that already answered as
Axis devices, and only as HTTP Digest answers, never as a plain password: the scan cannot verify any certificate.
"""

from __future__ import annotations

import copy
import errno
import ipaddress
import math
import queue
import re
import selectors
import socket
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from functools import partial
from typing import Any

from ax_devil_device_api import Client, DeviceConfig
from ax_devil_device_api.core.config import AuthMethod
from ax_devil_device_api.utils.errors import AuthenticationError

from ax_devil.modules.settings.config_manager import ConfigManager, expand_environment_reference
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

MAX_SCAN_TARGETS = 4096
# Tried in this order on a device with both open; plain HTTP answers faster and needs no credentials here.
_PORT_CONFIGS: dict[int, Callable[[str, str, str], Any]] = {80: DeviceConfig.http, 443: DeviceConfig.https}
_CONNECT_TIMEOUT_SECONDS = 0.5
_MAX_OPEN_SOCKETS = 256
_CONNECT_IN_PROGRESS = frozenset((errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK))
_REQUEST_TIMEOUT_SECONDS = 2.0
_IDENTIFY_WORKERS = 32
_IDENTIFY_DEADLINE_SECONDS = 20.0  # For the requests still running once the probe has finished.
_RATE_LIMIT_RETRY_SECONDS = (1.0, 2.0)  # Waits before trying a login again that a device turned away for now.
_HTTP_STATUS = re.compile(r"\bHTTP\s+(\d{3})\b")


@dataclass(frozen=True)
class Login:
    """A username and password to sign in with, as written, so a ``$VARIABLE`` reference stays one until used."""

    username: str
    password: str

    @staticmethod
    def saved(config: ConfigManager) -> tuple[Login, ...]:
        """Return the logins Settings gives the network scan to try, in order."""
        return Login.from_config(config.get_raw("defaults")["network_scan"].get("credentials"))

    @staticmethod
    def save(config: ConfigManager, logins: Iterable[Login]) -> None:
        """Make *logins* the ones the network scan tries, and save the config."""
        defaults = copy.deepcopy(config.get_raw("defaults"))  # So a failed save leaves the live config untouched.
        defaults["network_scan"]["credentials"] = Login.config_value(logins)
        config.set("defaults", defaults)
        config.save()

    @staticmethod
    def from_config(value: object) -> tuple[Login, ...]:
        """Return the logins a config list of ``{"username", "password"}`` objects holds, skipping any malformed one."""
        entries = value if isinstance(value, list) else []
        return tuple(
            Login(entry["username"], entry.get("password", ""))
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("username"), str)
            and entry["username"]
            and isinstance(entry.get("password", ""), str)
        )

    @staticmethod
    def config_value(logins: Iterable[Login]) -> list[dict[str, str]]:
        """Return *logins* as the config stores them."""
        return [{"username": login.username, "password": login.password} for login in logins]


class SignIn(Enum):
    """Whether the scan signed in to a device, as the Live view says it."""

    NOT_TRIED = "Not tried"
    SIGNED_IN = "Signed in"
    REFUSED = "Credentials refused"
    FAILED = "Sign-in failed"

    @property
    def label(self) -> str:
        """Return the text shown for this state."""
        return self.value


@dataclass(frozen=True)
class DiscoveredCamera:
    """An Axis device that answered the network scan; architecture and SoC are known only once signed in, and *login*
    is the one the device accepted."""

    host: str
    model: str
    serial: str
    firmware: str = ""
    product_type: str = ""
    uptime_seconds: float | None = None
    architecture: str = ""
    soc: str = ""
    sign_in: SignIn = SignIn.NOT_TRIED
    login: Login | None = None

    @property
    def uptime(self) -> str:
        """Return how long the device has been up, such as ``3d 4h``, or empty when unknown."""
        if self.uptime_seconds is None:
            return ""
        minutes, hours, days = self.uptime_seconds / 60, self.uptime_seconds / 3600, self.uptime_seconds / 86400
        if days >= 1:
            return f"{int(days)}d {int(hours % 24)}h"
        if hours >= 1:
            return f"{int(hours)}h {int(minutes % 60)}m"
        return f"{int(minutes)}m"

    @property
    def facets(self) -> tuple[tuple[str, str], ...]:
        """Return the ``(group, value)`` pairs the device can be filtered by."""
        return (("Type", self.product_type or "Unknown"), ("Sign-in", self.sign_in.label))


class DeviceOrder(Enum):
    """The orders found devices can be listed in, by their label; each puts devices missing the value last."""

    ADDRESS = "Address"
    MODEL = "Model"
    FIRMWARE = "Firmware"
    UPTIME = "Uptime"

    @property
    def label(self) -> str:
        """Return the text shown for this order."""
        return self.value

    def sorted(self, cameras: Iterable[DiscoveredCamera]) -> list[DiscoveredCamera]:
        """Return *cameras* in this order, by address among equals."""
        by_address = sorted(cameras, key=lambda camera: ipaddress.IPv4Address(camera.host))
        return sorted(by_address, key=_ORDER_KEYS[self])


def _version(text: str) -> tuple[int, ...]:
    """Return the numbers of a firmware version, so ``12.10`` sorts after ``12.9``."""
    return tuple(int(number) for number in re.findall(r"\d+", text))


_ORDER_KEYS: dict[DeviceOrder, Callable[[DiscoveredCamera], tuple[Any, ...]]] = {
    DeviceOrder.ADDRESS: lambda _camera: (),
    DeviceOrder.MODEL: lambda camera: (not camera.model, camera.model.casefold()),
    DeviceOrder.FIRMWARE: lambda camera: (not camera.firmware, _version(camera.firmware)),
    DeviceOrder.UPTIME: lambda camera: (camera.uptime_seconds is None, camera.uptime_seconds or 0.0),
}


def parse_scan_targets(text: str) -> tuple[str, ...]:
    """Return the IPv4 addresses *text* names, in address order and without repeats; empty text names none.

    *text* is addresses and CIDR ranges such as ``192.168.1.0/24`` (its host addresses, not the network and broadcast
    addresses), separated by commas or spaces. Raise ``ValueError`` for anything else, or when the addresses named
    add up to more than ``MAX_SCAN_TARGETS``; ranges are merged and counted before any is expanded, so pasting a huge
    list is cheap.
    """
    spans: set[tuple[int, int]] = set()  # First and last address of each token, as integers.
    for token in text.replace(",", " ").split():
        try:
            network = ipaddress.IPv4Network(token, strict=False)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
            raise ValueError(f"{token} is not an IPv4 address or range such as 192.168.1.0/24.") from None
        hosts = network.prefixlen >= 31
        spans.add((int(network[0]) + (not hosts), int(network[-1]) - (not hosts)))
    merged: list[list[int]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    if sum(last - first + 1 for first, last in merged) > MAX_SCAN_TARGETS:
        raise ValueError(f"Enter at most {MAX_SCAN_TARGETS} addresses.")
    return tuple(str(ipaddress.IPv4Address(address)) for first, last in merged for address in range(first, last + 1))


def scan_network(
    *,
    targets: Sequence[str] = (),
    logins: Sequence[Login] = (),
    found: Callable[[DiscoveredCamera], None] = lambda _camera: None,
) -> list[DiscoveredCamera]:
    """Return the Axis devices at *targets*, or on this computer's /24 network if there are none, sorted by address.

    Each device is handed to *found*, on a worker thread, as soon as it is identified, and never after the scan
    returns. With *logins*, the scan also tries them in order on each device until one signs in, for what only a
    signed-in user may read.

    Blocks for up to half a second per 128 addresses plus the device requests, so call it from a worker thread. Raise
    ``OSError`` if the local network cannot be worked out or the probe cannot open sockets.
    """
    hosts = targets or _local_network()
    cameras: list[DiscoveredCamera] = []
    lock = threading.Lock()
    finished = threading.Event()
    pending: queue.SimpleQueue[tuple[str, set[int]] | None] = queue.SimpleQueue()

    def work() -> None:
        while not finished.is_set() and (job := pending.get()) is not None:
            camera = _identify(*job, logins, finished)
            with lock:
                if camera is not None and not finished.is_set():
                    cameras.append(camera)
                    found(camera)

    # Daemon threads, unlike a ThreadPoolExecutor's workers, are not joined at exit, so a slow device cannot hold it.
    workers = [threading.Thread(target=work, name="network-scan", daemon=True) for _ in range(_IDENTIFY_WORKERS)]
    for worker in workers:
        worker.start()
    try:
        try:
            per_batch = _MAX_OPEN_SOCKETS // len(_PORT_CONFIGS)
            for start in range(0, len(hosts), per_batch):
                for host, ports in _open_ports(hosts[start : start + per_batch]).items():
                    pending.put((host, ports))
        finally:
            for _worker in workers:
                pending.put(None)
        deadline = time.monotonic() + _IDENTIFY_DEADLINE_SECONDS
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
    finally:
        with lock:
            finished.set()
    return DeviceOrder.ADDRESS.sorted(cameras)


def _local_network() -> tuple[str, ...]:
    """Return the host addresses of the /24 around this computer's address on its default route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))  # Picks the outgoing interface; connecting UDP sends nothing.
        address: str = sock.getsockname()[0]
    return parse_scan_targets(f"{address}/24")


def _open_ports(hosts: Sequence[str]) -> dict[str, set[int]]:
    """Return the web ports among *hosts* that accept a TCP connection in time, for each host that has any."""
    found: dict[str, set[int]] = {}
    with selectors.DefaultSelector() as selector:
        try:
            for host in hosts:
                for port in _PORT_CONFIGS:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setblocking(False)
                    result = sock.connect_ex((host, port))
                    if result in _CONNECT_IN_PROGRESS:
                        selector.register(sock, selectors.EVENT_WRITE, (sock, host, port))
                        continue
                    if result == 0:
                        found.setdefault(host, set()).add(port)
                    sock.close()
            deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
            while selector.get_map() and (remaining := deadline - time.monotonic()) > 0:
                for key, _events in selector.select(remaining):
                    sock, host, port = key.data
                    selector.unregister(sock)
                    if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                        found.setdefault(host, set()).add(port)
                    sock.close()
        finally:
            for key in list(selector.get_map().values()):
                key.data[0].close()
    return found


def _identify(
    host: str, ports: Collection[int], logins: Sequence[Login], cancelled: threading.Event
) -> DiscoveredCamera | None:
    """Return the Axis device at *host* from what it gives without login, signed in to with the first of *logins* it
    accepts, or ``None`` if no Axis device answers."""
    for port, make in _PORT_CONFIGS.items():
        if port not in ports:
            continue
        config = make(host, "", "")
        config.timeout = _REQUEST_TIMEOUT_SECONDS
        try:
            with Client(config) as client:
                info: Mapping[str, Any] = client.device.get_info_no_auth()
                camera = DiscoveredCamera(
                    host=host,
                    model=_text(info, "ProdShortName") or _text(info, "ProdFullName") or _text(info, "ProdNbr"),
                    serial=_text(info, "SerialNumber"),
                    firmware=_text(info, "Version"),
                    product_type=_text(info, "ProdType"),
                    uptime_seconds=_uptime_seconds(client),
                )
        except Exception as exc:  # Anything on the range can answer, in any shape; none of that is an Axis device.
            logger.debug(f"Network scan could not identify {host} over {config.protocol.value}: {exc}")
            continue
        if not logins:
            return camera
        secure = _PORT_CONFIGS[443] if 443 in ports else make  # Credentials go over HTTPS whenever the device has it.
        return _signed_in(camera, partial(secure, host), logins, cancelled)
    return None


def _signed_in(
    camera: DiscoveredCamera, make: Callable[[str, str], Any], logins: Sequence[Login], cancelled: threading.Event
) -> DiscoveredCamera:
    """Return *camera* with what signing in through configs from *make* tells, with the first of *logins* it accepts.

    A login whose username expands to nothing is skipped, and no login is sent once *cancelled* is set.

    A login the device turns away for now, as Axis devices do after failed attempts, is tried again after a wait. Any
    other failure ends the attempt, so a broken device is not asked again and again.
    """
    refusals = 0
    tried = False
    for login in logins:
        username = expand_environment_reference(login.username)
        if not username:
            continue
        tried = True
        for wait in (*_RATE_LIMIT_RETRY_SECONDS, None):
            if cancelled.is_set():
                return camera
            config = make(username, expand_environment_reference(login.password))
            config.timeout = _REQUEST_TIMEOUT_SECONDS
            config.auth_method = AuthMethod.DIGEST
            try:
                with Client(config) as client:
                    info: Mapping[str, Any] = client.device.get_info_auth()
                architecture, soc = _text(info, "Architecture"), _text(info, "Soc")
                return replace(camera, architecture=architecture, soc=soc, sign_in=SignIn.SIGNED_IN, login=login)
            except AuthenticationError:
                refusals += 1
                break
            except Exception as exc:
                if wait is None or not _rate_limited(exc, refusals):
                    logger.debug(f"Network scan could not sign in to {camera.host}: {exc}")
                    return replace(camera, sign_in=SignIn.FAILED)
                cancelled.wait(wait)
    return replace(camera, sign_in=SignIn.REFUSED) if tried else camera


def _rate_limited(error: Exception, refusals: int) -> bool:
    """Return whether a failed sign-in looks like the device turning logins away for now."""
    match = _HTTP_STATUS.search(str(error))
    status = int(match.group(1)) if match else None
    return status == 429 or (status == 403 and refusals > 0)


def _uptime_seconds(client: Any) -> float | None:
    """Return how many seconds the device has been up, from ``systemready.cgi``, or ``None`` if it does not say."""
    try:
        seconds = float(client.systemready.systemready(timeout=1).get("uptime"))
    except Exception as exc:  # Older firmware lacks the API, and any answer is untrusted.
        logger.debug(f"Network scan got no uptime: {exc}")
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _text(info: Mapping[str, Any], name: str) -> str:
    """Return the device information property *name* as stripped text, or empty if it is missing or not text."""
    value = info.get(name)
    return value.strip() if isinstance(value, str) else ""
