"""Common payload-to-scene decoder interface shared by streaming and file data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from ax_devil.modules.scene.model import Scene


class PayloadToSceneDecoder(ABC):
    """Stateful (if needed) decoder that emits :class:`Scene` objects."""

    @abstractmethod
    def decode(self, payload: Any) -> Scene | None:
        """Translate one payload into a :class:`Scene`, or ``None`` when no scene should be emitted."""


PayloadToSceneDecoderFactory = Callable[[], PayloadToSceneDecoder]

__all__ = ["PayloadToSceneDecoder", "PayloadToSceneDecoderFactory"]
