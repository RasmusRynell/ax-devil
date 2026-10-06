"""Shared interaction protocol types for video player orchestration."""

from __future__ import annotations

from typing import Protocol


class HoverSink(Protocol):
    """Target that receives hover enter/leave notifications from fading widgets."""

    def on_hover_enter(self, element_name: str) -> None: ...

    def on_hover_leave(self, element_name: str) -> None: ...
