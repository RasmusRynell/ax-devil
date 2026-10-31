"""Credential-safe diagnostics at the MQTT dependency boundary."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, quote_plus

from ax_devil_device_api.utils.errors import AuthenticationError, ConfigurationError


class UnsupportedAnalyticsDataSourceError(ValueError):
    """Raised when a configured analytics source is not available on the device."""


class MqttDiagnostics:
    """Redact this connection's passwords in exceptions and dependency log records."""

    def __init__(self, passwords: tuple[str, ...]) -> None:
        self._secrets = sorted(
            {
                encoded
                for value in passwords
                if value
                for encoded in (
                    value,
                    quote(value, safe=""),
                    quote_plus(value),
                    json.dumps(value)[1:-1],
                    repr(value)[1:-1],
                )
            },
            key=len,
            reverse=True,
        )

    def redact(self, message: str) -> str:
        """Keep the failure reason while removing URL userinfo and known passwords."""
        message = re.sub(r"(\w+://)[^/\s@]+@", r"\1[redacted]@", message)
        for secret in self._secrets:
            message = message.replace(secret, "[redacted]")
        return message


def terminal_mqtt_failure(error: Exception) -> bool:
    """Recognize typed device rejections and the broker CONNACK codes exposed by ax-devil-mqtt."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (AuthenticationError, ConfigurationError, UnsupportedAnalyticsDataSourceError)):
            return True
        # The dependency currently exposes CONNACK as text, rather than a typed error/code.
        match = re.fullmatch(r"Failed to connect to MQTT broker with code (\d+)", str(current))
        if match and int(match[1]) in {1, 2, 4, 5}:
            return True
        current = current.__cause__ or current.__context__
    return False
