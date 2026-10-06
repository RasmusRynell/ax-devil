"""Overlay persistence helpers for controller-managed sticky overlays."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from ax_devil.core.data_types import FrameIdentifier, OverlayData
from ax_devil.modules.data_sources.base import OverlayLookup


@dataclass(slots=True)
class OverlayPersistenceSettings:
    """Configuration used when deciding whether to reuse previous overlays."""

    enabled: bool = False
    timeout_ms: Optional[int] = 2050
    opacity: float = 1.0

    def copy(self) -> "OverlayPersistenceSettings":
        """Return a shallow copy of the current settings."""
        return replace(self)

    @classmethod
    def default_enabled(cls) -> "OverlayPersistenceSettings":
        """Return settings with sticky overlays enabled."""
        return cls(enabled=True)


@dataclass(slots=True)
class OverlaySelection:
    """Determines which overlay to display and how to present it."""

    overlay: Optional[OverlayData]
    reused: bool
    effective_opacity: float

    def to_metadata(self) -> dict[str, float | bool]:
        """Return overlay metadata for renderer consumption."""
        return {
            "overlay_reused": self.reused,
            "overlay_opacity": self.effective_opacity,
        }


def _clamp_opacity(value: float) -> float:
    """Ensure opacity stays within [0.0, 1.0]."""
    return max(0.0, min(1.0, value))


class OverlayPersistencePolicy:
    """Select overlays by sample age, using indexed files or the latest live sample.

    Offline selection queries the source independently for each frame. Live selection
    retains the newest sample matched by synchronization. Both measure expiry from the sample
    timestamp, so pausing or changing playback speed does not affect its age.
    """

    def __init__(
        self,
        settings: OverlayPersistenceSettings | None = None,
    ) -> None:
        initial = replace(settings) if settings is not None else OverlayPersistenceSettings()
        initial.opacity = _clamp_opacity(initial.opacity)
        self._settings = initial
        self._cached_overlay: OverlayData | None = None

    @property
    def settings(self) -> OverlayPersistenceSettings:
        """Return current settings."""
        return self._settings

    def update_settings(self, new_settings: OverlayPersistenceSettings) -> None:
        """Replace settings in preparation for configurable behaviour."""
        updated = replace(new_settings)
        updated.opacity = _clamp_opacity(updated.opacity)
        self._settings = updated
        if not self._settings.enabled:
            self.reset()

    def reset(self) -> None:
        """Clear cached overlay information."""
        self._cached_overlay = None

    def sample_selection(self) -> tuple[bool, int | None]:
        """Return whether offline lookup may retain earlier samples, and the oldest sample age it shows in µs."""
        timeout_ms = self._settings.timeout_ms if self._settings.enabled else None
        return self._settings.enabled, timeout_ms * 1000 if timeout_ms is not None else None

    def select_from_source(self, source: OverlayLookup, frame_id: FrameIdentifier) -> OverlaySelection:
        """Select offline data by sample time, independent of previously displayed frames."""
        candidate = source.get_overlay_at_frame(frame_id, allow_previous=self._settings.enabled)
        if candidate is None or not self._is_eligible(frame_id, candidate):
            return OverlaySelection(None, False, self._settings.opacity)
        reused = (candidate.metadata or {}).get("timestamp_match_type") == "retained"
        return OverlaySelection(candidate, reused, self._settings.opacity if reused else 1.0)

    def select_overlay(
        self,
        frame_id: FrameIdentifier,
        candidate: Optional[OverlayData],
    ) -> OverlaySelection:
        """Determine which overlay should be forwarded to the renderer.

        Candidates have already passed live synchronization matching.
        When disabled, eligible candidates pass through without retention.
        When enabled, the policy caches the latest overlay and reuses it while
        the timeout (measured in video time) has not elapsed. Opacity metadata
        is precomputed for the caller so the renderer can highlight reused overlays.
        """
        if candidate is not None and not self._is_eligible(frame_id, candidate):
            candidate = None
        if not self._settings.enabled:
            return OverlaySelection(candidate, False, 1.0)

        cached = self._cached_overlay
        if cached is not None and not self._is_eligible(frame_id, cached):
            self.reset()
            cached = None
        if candidate is not None and (
            cached is None or candidate.frame_id.timestamp_monotime_us >= cached.frame_id.timestamp_monotime_us
        ):
            self._cached_overlay = candidate
        cached = self._cached_overlay
        if cached is None:
            return OverlaySelection(None, False, self._settings.opacity)
        reused = cached is not candidate
        return OverlaySelection(cached, reused, self._settings.opacity if reused else 1.0)

    def _is_eligible(self, frame_id: FrameIdentifier, overlay: OverlayData) -> bool:
        age_us = frame_id.timestamp_monotime_us - overlay.frame_id.timestamp_monotime_us
        timeout_ms = self._settings.timeout_ms if self._settings.enabled else None
        return age_us >= 0 and (timeout_ms is None or age_us <= timeout_ms * 1000)
