"""Find Axis cameras with an SSDP search, on the local network or at addresses the user names.

Everything on the LAN can answer an SSDP search, so each answer is untrusted: only an ``http://`` description URL on
the answering address is fetched, the download is size-limited, and XML that declares a DTD is never parsed.
"""

from __future__ import annotations

import http.client
import ipaddress
import queue
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPDefaultErrorHandler, HTTPErrorProcessor, HTTPHandler, OpenerDirector, Request

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900


def _search_request(destination: str) -> bytes:
    """Return the M-SEARCH for *destination*, whose address and port the HOST header must carry."""
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {destination}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: upnp:rootdevice\r\n"
        "\r\n"
    ).encode("ascii")


_FETCH_TIMEOUT_SECONDS = 2.0
_FETCH_DEADLINE_SECONDS = 5.0  # For the whole download: a device that trickles bytes cannot hold a worker.
_MAX_DESCRIPTION_BYTES = 64 * 1024
_MAX_RESPONDERS = 128
MAX_SCAN_TARGETS = 4096
_FETCH_WORKERS = 16
_SCAN_DEADLINE_SECONDS = 20.0  # For all description downloads together.
_SERIAL_SUFFIX = re.compile(r"[0-9A-Fa-f]{12}")
_AXIS_MANUFACTURER = re.compile(r"\baxis\b", re.IGNORECASE)  # Not "Praxis".


@dataclass(frozen=True)
class DiscoveredCamera:
    """An Axis camera that answered the network scan."""

    host: str
    model: str
    serial: str


@dataclass(frozen=True)
class SsdpResponse:
    """One SSDP answer: the IPv4 address it came from and the description URL it advertised."""

    address: str
    location: str


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


class ScanTransport(Protocol):
    """The network access a scan needs, replaceable in tests."""

    def search(self, timeout: float, targets: Sequence[str]) -> list[SsdpResponse]:
        """Send one SSDP search to each of *targets*, or to the whole local network if none; return the answers
        received within ``timeout`` seconds."""
        ...

    def fetch(self, url: str) -> str:
        """Return the body of ``url`` as text, or raise ``OSError`` or ``ValueError`` if it cannot be read."""
        ...


class NetworkScanTransport:
    """Real transport: SSDP over UDP multicast and plain HTTP without redirects or proxies."""

    def search(self, timeout: float, targets: Sequence[str]) -> list[SsdpResponse]:
        """Send one M-SEARCH to each of *targets*, or to the SSDP multicast group if none, and collect answers until
        ``timeout`` elapses.

        Only the first valid answer of each address is kept, and only for the first 128 addresses, so a device that
        floods the network with replies cannot grow the result. Given *targets*, only they are heard. Raise the last
        ``OSError`` if the search could not be sent to any destination.
        """
        destinations = list(targets) or [SSDP_GROUP]
        responses: dict[str, SsdpResponse] = {}
        overflowed = False
        deadline = time.monotonic() + timeout
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sent = 0
            failure: OSError | None = None
            for destination in destinations:
                try:
                    sock.sendto(_search_request(destination), (destination, SSDP_PORT))
                    sent += 1
                except OSError as exc:
                    failure = exc
            if failure is not None and not sent:
                raise failure
            while (remaining := deadline - time.monotonic()) > 0:
                sock.settimeout(remaining)
                try:
                    data, (address, _port) = sock.recvfrom(8192)
                except TimeoutError:
                    break
                except ConnectionResetError:  # Windows reports an unreachable target's ICMP error on the next read.
                    continue
                location = _location_header(data)
                if location is None or address in responses or not _is_own_http_url(location, address):
                    continue
                if targets and address not in targets:
                    continue
                if len(responses) < _MAX_RESPONDERS:
                    responses[address] = SsdpResponse(address=address, location=location)
                else:
                    overflowed = True
        if overflowed:
            logger.warning(f"Network scan saw more than {_MAX_RESPONDERS} responders; checking the first ones")
        return list(responses.values())

    def fetch(self, url: str) -> str:
        """Download at most 64 KiB from ``url``; a larger body is rejected rather than truncated."""
        opener = OpenerDirector()
        for handler in (HTTPHandler(), HTTPDefaultErrorHandler(), HTTPErrorProcessor()):
            opener.add_handler(handler)
        deadline = time.monotonic() + _FETCH_DEADLINE_SECONDS
        chunks: list[bytes] = []
        size = 0
        try:
            with opener.open(Request(url), timeout=_FETCH_TIMEOUT_SECONDS) as response:
                while chunk := response.read1(
                    min(8192, _MAX_DESCRIPTION_BYTES + 1 - size)
                ):  # At most one network read.
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _MAX_DESCRIPTION_BYTES:
                        raise ValueError(f"{url} is larger than {_MAX_DESCRIPTION_BYTES} bytes")
                    if time.monotonic() > deadline:
                        raise OSError(f"{url} is too slow")
        except (URLError, http.client.HTTPException) as exc:
            raise OSError(f"cannot fetch {url}: {exc}") from exc
        return b"".join(chunks).decode("utf-8", errors="replace")


def scan_network(
    *, targets: Sequence[str] = (), timeout: float = 3.0, transport: ScanTransport | None = None
) -> list[DiscoveredCamera]:
    """Return the Axis cameras that answer an SSDP search, de-duplicated by host and sorted by IP address.

    The search goes to *targets*, or to the whole local network if there are none.

    Blocks for about ``timeout`` seconds plus the description downloads, so call it from a worker thread. A network
    that cannot be searched yields an empty list, and a device with an unusable answer is skipped.
    """
    transport = transport or NetworkScanTransport()
    try:
        responses = transport.search(timeout, targets)
    except OSError as exc:
        logger.warning(f"Network scan could not search the network: {exc}")
        return []

    locations: dict[str, str] = {}
    for response in responses:
        if response.address not in locations and _is_own_http_url(response.location, response.address):
            locations[response.address] = response.location
    cameras = _describe_all(transport, locations)
    return sorted(cameras, key=lambda camera: int(ipaddress.IPv4Address(camera.host)))


def _describe_all(transport: ScanTransport, locations: dict[str, str]) -> list[DiscoveredCamera]:
    """Describe every host on daemon threads; hosts still waiting when the batch deadline passes are abandoned.

    Daemon threads (not a ``ThreadPoolExecutor``, whose workers are joined at interpreter exit) keep a slow
    responder from delaying application exit.
    """
    deadline = time.monotonic() + _SCAN_DEADLINE_SECONDS
    pending: queue.SimpleQueue[str] = queue.SimpleQueue()
    for host in locations:
        pending.put(host)
    cameras: list[DiscoveredCamera] = []

    def work() -> None:
        while time.monotonic() < deadline:
            try:
                host = pending.get_nowait()
            except queue.Empty:
                return
            camera = _describe(transport, host, locations[host])
            if camera is not None:
                cameras.append(camera)

    workers = [
        threading.Thread(target=work, name="network-scan", daemon=True)
        for _ in range(min(_FETCH_WORKERS, len(locations)))
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    return list(cameras)


def _location_header(datagram: bytes) -> str | None:
    """Return the LOCATION header of an SSDP answer, or ``None`` if it is not a successful answer."""
    lines = datagram.decode("utf-8", errors="replace").split("\r\n")
    if lines[0].split()[1:2] != ["200"]:
        return None
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "location":
            return value.strip()
    return None


def _is_own_http_url(url: str, address: str) -> bool:
    """Whether ``address`` is an IPv4 address and ``url`` is a plain ``http://`` URL on that same address."""
    try:
        ipaddress.IPv4Address(address)
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname == address and parts.username is None


def _describe(transport: ScanTransport, host: str, url: str) -> DiscoveredCamera | None:
    """Fetch and parse one device description; ``None`` for anything that is not a readable Axis device."""
    try:
        return _parse_axis_description(host, transport.fetch(url))
    except (OSError, ValueError, ET.ParseError, http.client.HTTPException) as exc:
        logger.debug(f"Network scan ignored {host}: {exc}")
        return None


def _parse_axis_description(host: str, document: str) -> DiscoveredCamera | None:
    """Read a UPnP device description; ``None`` unless the manufacturer is Axis."""
    if "<!doctype" in document.lower() or "<!entity" in document.lower():
        raise ValueError("device description declares a DTD")
    root = ET.fromstring(document)

    def text(name: str) -> str:
        for element in root.iter():
            if element.tag.rpartition("}")[2] == name and element.text and element.text.strip():
                return element.text.strip()
        return ""

    if not _AXIS_MANUFACTURER.search(text("manufacturer")):
        return None
    serial = text("serialNumber")
    model = text("modelName") or _without_serial(text("friendlyName"), serial)
    return DiscoveredCamera(host=host, model=model, serial=serial)


def _without_serial(friendly_name: str, serial: str) -> str:
    """Drop a trailing `` - <serial>`` (the serial number or a 12-digit MAC) from a friendly name."""
    name, separator, suffix = friendly_name.rpartition(" - ")
    if separator and (suffix == serial or _SERIAL_SUFFIX.fullmatch(suffix)):
        return name
    return friendly_name
