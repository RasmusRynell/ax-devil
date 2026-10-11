"""Find Axis cameras on the local network with an SSDP search.

Everything on the LAN can answer an SSDP search, so each answer is untrusted: only an ``http://`` description URL on
the answering address is fetched, the download is size-limited, and XML that declares a DTD is never parsed.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPDefaultErrorHandler, HTTPErrorProcessor, HTTPHandler, OpenerDirector, Request

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
_SEARCH_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: upnp:rootdevice\r\n"
    "\r\n"
).encode("ascii")

_FETCH_TIMEOUT_SECONDS = 2.0
_MAX_DESCRIPTION_BYTES = 64 * 1024
_MAX_RESPONDERS = 128
_FETCH_WORKERS = 16
_SERIAL_SUFFIX = re.compile(r"[0-9A-Fa-f]{12}")


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


class ScanTransport(Protocol):
    """The network access a scan needs, replaceable in tests."""

    def search(self, timeout: float) -> list[SsdpResponse]:
        """Send one SSDP search and return every answer received within ``timeout`` seconds."""
        ...

    def fetch(self, url: str) -> str:
        """Return the body of ``url`` as text, or raise ``OSError`` or ``ValueError`` if it cannot be read."""
        ...


class NetworkScanTransport:
    """Real transport: SSDP over UDP multicast and plain HTTP without redirects or proxies."""

    def search(self, timeout: float) -> list[SsdpResponse]:
        """Send one M-SEARCH to the SSDP multicast group and collect answers until ``timeout`` elapses."""
        responses: list[SsdpResponse] = []
        deadline = time.monotonic() + timeout
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.sendto(_SEARCH_REQUEST, (SSDP_GROUP, SSDP_PORT))
            while (remaining := deadline - time.monotonic()) > 0:
                sock.settimeout(remaining)
                try:
                    data, (address, _port) = sock.recvfrom(8192)
                except TimeoutError:
                    break
                location = _location_header(data)
                if location is not None:
                    responses.append(SsdpResponse(address=address, location=location))
        return responses

    def fetch(self, url: str) -> str:
        """Download at most 64 KiB from ``url``; a larger body is rejected rather than truncated."""
        opener = OpenerDirector()
        for handler in (HTTPHandler(), HTTPDefaultErrorHandler(), HTTPErrorProcessor()):
            opener.add_handler(handler)
        try:
            with opener.open(Request(url), timeout=_FETCH_TIMEOUT_SECONDS) as response:
                body: bytes = response.read(_MAX_DESCRIPTION_BYTES + 1)
        except URLError as exc:
            raise OSError(f"cannot fetch {url}: {exc}") from exc
        if len(body) > _MAX_DESCRIPTION_BYTES:
            raise ValueError(f"{url} is larger than {_MAX_DESCRIPTION_BYTES} bytes")
        return body.decode("utf-8", errors="replace")


def scan_network(*, timeout: float = 3.0, transport: ScanTransport | None = None) -> list[DiscoveredCamera]:
    """Return the Axis cameras that answer an SSDP search, de-duplicated by host and sorted by IP address.

    Blocks for about ``timeout`` seconds plus the description downloads, so call it from a worker thread. A network
    that cannot be searched yields an empty list, and a device with an unusable answer is skipped.
    """
    transport = transport or NetworkScanTransport()
    try:
        responses = transport.search(timeout)
    except OSError as exc:
        logger.warning(f"Network scan could not search the network: {exc}")
        return []

    locations: dict[str, str] = {}
    for response in responses:
        if response.address not in locations and _is_own_http_url(response.location, response.address):
            locations[response.address] = response.location
    if len(locations) > _MAX_RESPONDERS:
        logger.warning(f"Network scan saw {len(locations)} responders; checking the first {_MAX_RESPONDERS}")
    hosts = list(locations)[:_MAX_RESPONDERS]

    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        found = list(pool.map(lambda host: _describe(transport, host, locations[host]), hosts))
    cameras = [camera for camera in found if camera is not None]
    return sorted(cameras, key=lambda camera: int(ipaddress.IPv4Address(camera.host)))


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
    except (OSError, ValueError, ET.ParseError) as exc:
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

    if "axis" not in text("manufacturer").lower():
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
