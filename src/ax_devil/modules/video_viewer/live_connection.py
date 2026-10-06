"""Connection status of the video and overlay feeds behind one live Video Viewer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum


class LiveConnectionState(Enum):
    """Connection state of one live feed, with the text and color used to show it."""

    CONNECTING = ("Connecting…", "Waiting for the device to answer.", "#f2c94c", False, False)
    LIVE = ("● Live", "Connected to the device.", "#5ad27a", False, False)
    STALLED = ("Stalled", "Connected, but no data has arrived recently.", "#f2994a", True, True)
    RECONNECTING = ("Reconnecting", "The connection failed; the source is trying again.", "#f2994a", True, False)
    FAILED = ("✕ Failed", "The connection failed and is not retried automatically.", "#ff6b6b", True, True)

    def __init__(self, label: str, description: str, color: str, is_problem: bool, needs_retry: bool) -> None:
        self.label = label
        self.description = description
        self.color = color
        self.is_problem = is_problem
        """Whether the viewer should show this state's reason to the user."""
        self.needs_retry = needs_retry
        """Whether only a manual retry can recover from this state."""


class LiveFeed(Enum):
    """One independently connected stream of live data."""

    VIDEO = "Video"
    OVERLAY = "Overlay"

    @property
    def label(self) -> str:
        """Return the user-facing feed name."""
        return self.value


@dataclass(frozen=True)
class LiveFeedStatus:
    """Connection state of one feed, with the reason for any problem."""

    feed: LiveFeed
    state: LiveConnectionState = LiveConnectionState.CONNECTING
    reason: str = ""
    attempts: int = 0

    @property
    def headline(self) -> str:
        """Return the short state text, including the retry count while reconnecting."""
        if self.attempts:
            return f"{self.state.label} ({self.attempts})"
        return self.state.label

    @property
    def summary(self) -> str:
        """Return one line naming the feed, its state and the reason."""
        return f"{self.feed.label}: {self.headline} — {self.reason or self.state.description}"

    def live(self) -> LiveFeedStatus:
        """Return the status after the feed connected or delivered data."""
        return LiveFeedStatus(self.feed, LiveConnectionState.LIVE)

    def reconnecting(self, reason: str) -> LiveFeedStatus:
        """Return the status after a failed attempt that the source retries itself."""
        return LiveFeedStatus(self.feed, LiveConnectionState.RECONNECTING, reason, self.attempts + 1)

    def failed(self, reason: str) -> LiveFeedStatus:
        """Return the status after a failure that needs a manual retry."""
        return LiveFeedStatus(self.feed, LiveConnectionState.FAILED, reason)

    def stalled(self, seconds: float) -> LiveFeedStatus:
        """Return the status after a live feed delivered nothing for ``seconds``."""
        if self.state is not LiveConnectionState.LIVE:
            return self
        reason = f"No {self.feed.label.lower()} received for {seconds:.0f} s."
        return replace(self, state=LiveConnectionState.STALLED, reason=reason)


@dataclass(frozen=True)
class LiveConnectionStatus:
    """Connection status of every feed in one live stream; the video feed comes first."""

    feeds: tuple[LiveFeedStatus, ...]

    @classmethod
    def connecting(cls, feeds: Iterable[LiveFeed]) -> LiveConnectionStatus:
        """Return a status where every feed is connecting."""
        return cls(tuple(LiveFeedStatus(feed) for feed in feeds))

    def feed(self, feed: LiveFeed) -> LiveFeedStatus:
        """Return the status of one feed."""
        return next(status for status in self.feeds if status.feed is feed)

    def with_status(self, status: LiveFeedStatus) -> LiveConnectionStatus:
        """Return a copy with one feed's status replaced."""
        return LiveConnectionStatus(tuple(status if item.feed is status.feed else item for item in self.feeds))

    @property
    def headline(self) -> LiveFeedStatus:
        """Return the status that represents the whole stream: the video feed."""
        return self.feeds[0]

    @property
    def problems(self) -> tuple[LiveFeedStatus, ...]:
        """Return the feeds whose reasons should be shown to the user."""
        return tuple(status for status in self.feeds if status.state.is_problem)

    @property
    def needs_retry(self) -> bool:
        """Whether any feed can only recover through a manual retry."""
        return any(status.state.needs_retry for status in self.feeds)

    @property
    def placeholder_text(self) -> str:
        """Return the text for an empty video pane."""
        video = self.headline
        return f"{video.headline}\n\n{video.reason or video.state.description}"
