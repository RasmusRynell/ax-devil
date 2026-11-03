"""Lookup metadata for decoder-backed file overlay providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ax_devil.modules.data_sources.timing_reports import OverlayAlignmentBasis
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackPolicy, TimestampMatchType

OverlayLookupMatchType = Literal[TimestampMatchType, "sequence"]


@dataclass(frozen=True, slots=True)
class OverlayLookupResult:
    """Scene lookup result plus metadata describing how the lookup resolved."""

    scene: Scene | None
    requested_timestamp_us: int | None
    matched_timestamp_us: int | None
    match_type: OverlayLookupMatchType
    timestamp_fallback_policy: TimestampFallbackPolicy
    requested_sequence_id: int | None = None
    matched_sequence_id: int | None = None
    alignment_basis: OverlayAlignmentBasis = "timestamp"

    @property
    def offset_us(self) -> int | None:
        """Return matched-minus-requested timestamp offset when both timestamps are known."""
        if self.requested_timestamp_us is None or self.matched_timestamp_us is None:
            return None
        return self.matched_timestamp_us - self.requested_timestamp_us

    def to_overlay_metadata(self) -> dict[str, Any]:
        """Return metadata fields for ``OverlayData.metadata``."""
        metadata: dict[str, Any] = {
            "requested_timestamp_us": self.requested_timestamp_us,
            "matched_timestamp_us": self.matched_timestamp_us,
            "timestamp_match_type": self.match_type,
            "timestamp_tolerance_us": self.timestamp_fallback_policy.tolerance_us,
            "timestamp_effective_tolerance_us": self.timestamp_fallback_policy.effective_tolerance_us,
            "timestamp_fallback_mode": self.timestamp_fallback_policy.mode.value,
            "overlay_alignment_basis": self.alignment_basis,
            "requested_sequence_id": self.requested_sequence_id,
            "matched_sequence_id": self.matched_sequence_id,
        }
        if self.offset_us is not None:
            metadata["timestamp_offset_us"] = self.offset_us
        return metadata
