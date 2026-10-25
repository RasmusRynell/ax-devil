"""How a camera's live stream is doing, as a sidebar row shows it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import KindColor, StatusColor


class LiveStreamState(Enum):
    """The state of a camera's stream, most urgent first, with its mark and text template.

    Only LIVE is drawn in the live color; every other state differs by glyph, so no state relies on color alone.
    """

    FAILED = (Icon.WARNING, StatusColor.WARNING, "Failed: {reason}")
    RECONNECTING = (Icon.RETRY, StatusColor.WARNING, "Reconnecting ({attempts})")
    STALLED = (Icon.CLOCK, StatusColor.WARNING, "{reason}")
    CONNECTING = (Icon.STREAM_CONNECTING, KindColor.MUTED, "Connecting…")
    LIVE = (Icon.STREAMING, KindColor.LIVE, "Live")
    PAUSED = (Icon.PAUSE, KindColor.MUTED, "Paused")
    IDLE = (Icon.LIVE_VIDEO, KindColor.MUTED, "")

    def __init__(self, icon: Icon, color: KindColor | StatusColor, template: str) -> None:
        self.icon = icon
        self.color = color
        self.template = template


_URGENCY = {state: rank for rank, state in enumerate(LiveStreamState)}


@dataclass(frozen=True, slots=True)
class LiveStreamStatus:
    """The state of one viewer's stream with the *reason* it failed or stalled and the reconnect *attempts*."""

    state: LiveStreamState
    reason: str = ""
    attempts: int = 0

    @property
    def text(self) -> str:
        """Return what the row says of the state; empty for an idle camera."""
        return self.state.template.format(reason=self.reason, attempts=self.attempts)

    @property
    def mark(self) -> tuple[Icon, KindColor | StatusColor]:
        """Return the icon and color role that mark the state."""
        return self.state.icon, self.state.color

    @property
    def is_live(self) -> bool:
        """Return whether frames are arriving."""
        return self.state is LiveStreamState.LIVE

    @classmethod
    def of_viewers(cls, statuses: Iterable[LiveStreamStatus]) -> LiveStreamStatus:
        """Return the most urgent of the *statuses* of the viewers showing one camera; idle when there is none."""
        return min(statuses, key=lambda status: _URGENCY[status.state], default=cls(LiveStreamState.IDLE))
