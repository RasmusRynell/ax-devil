"""Resolving a video frame to the overlay scene a decoder-backed file provider shows for it."""

from __future__ import annotations

from collections.abc import Callable

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.file_data_provider.lookup_metadata import (
    OverlayLookupMatchType,
    OverlayLookupResult,
)
from ax_devil.modules.data_sources.file_data_provider.retrieval_stats import RetrievalStats
from ax_devil.modules.data_sources.overlay_samples import OverlaySamples, sequence_sample
from ax_devil.modules.data_sources.timing_reports import FrameTimeline, OverlayAlignmentBasis
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.synchronization.timestamp_matching import find_matching_overlay_timestamp


def lookup_scene(
    samples: OverlaySamples,
    load_scene: Callable[[int], Scene | None],
    stats: RetrievalStats,
    frame_identifier: FrameIdentifier,
    *,
    allow_previous: bool,
    frame_timeline: FrameTimeline | None,
) -> OverlayLookupResult:
    """Load scene data using FrameIdentifier with timestamp and optional sequence lookup.

    Frame-keyed providers use frame_timeline to resolve annotation sequence IDs
    into video time. Otherwise lookup uses exact timestamps, optional sequence
    fallback, then timestamp matching. With allow_previous, select the latest
    earlier sample beyond matching tolerance; the caller applies its age limit.
    Records the outcome in *stats*.
    """
    if samples.sequence_lookup:
        if frame_timeline is not None:
            return _lookup_video_sequence(
                samples, load_scene, stats, frame_identifier, frame_timeline, allow_previous=allow_previous
            )
        if allow_previous:
            raise ValueError("Sticky sequence overlays require the video's frame timeline")

    timestamp_key = int(frame_identifier.timestamp_monotime_us)

    scene = load_scene(timestamp_key)
    if scene is not None:
        result = _lookup_result(
            samples,
            scene=scene,
            requested_timestamp_us=timestamp_key,
            matched_timestamp_us=timestamp_key,
            requested_sequence_id=frame_identifier.sequence_id,
            matched_sequence_id=samples.timestamp_to_sequence.get(timestamp_key),
            match_type="exact",
        )
        stats.record(timestamp_key, stats.exact)
        return result

    if samples.sequence_lookup:
        ts_from_seq = samples.sequence_to_timestamp.get(frame_identifier.sequence_id)
        if ts_from_seq is not None and ts_from_seq <= timestamp_key:
            scene = load_scene(ts_from_seq)
            if scene is not None:
                result = _lookup_result(
                    samples,
                    scene=scene,
                    requested_timestamp_us=timestamp_key,
                    matched_timestamp_us=ts_from_seq,
                    requested_sequence_id=frame_identifier.sequence_id,
                    matched_sequence_id=frame_identifier.sequence_id,
                    alignment_basis="sequence",
                    match_type="sequence",
                )
                stats.record(timestamp_key, stats.resolved)
                return result

    match = find_matching_overlay_timestamp(
        sorted_overlay_timestamps_us=samples.timestamps_us,
        video_timestamp_us=timestamp_key,
        policy=samples.policy,
        allow_previous=allow_previous,
    )
    if match.overlay_timestamp_us is not None:
        scene = load_scene(match.overlay_timestamp_us)
        if scene is not None:
            result = _lookup_result(
                samples,
                scene=scene,
                requested_timestamp_us=timestamp_key,
                matched_timestamp_us=match.overlay_timestamp_us,
                requested_sequence_id=frame_identifier.sequence_id,
                matched_sequence_id=samples.timestamp_to_sequence.get(match.overlay_timestamp_us),
                match_type=match.match_type,
            )
            stats.record(timestamp_key, stats.resolved)
            return result

    result = _lookup_result(
        samples,
        scene=None,
        requested_timestamp_us=timestamp_key,
        matched_timestamp_us=None,
        requested_sequence_id=frame_identifier.sequence_id,
        matched_sequence_id=None,
        match_type="missing",
    )
    stats.record(timestamp_key, stats.misses)
    return result


def _lookup_video_sequence(
    samples: OverlaySamples,
    load_scene: Callable[[int], Scene | None],
    stats: RetrievalStats,
    frame_id: FrameIdentifier,
    timeline: FrameTimeline,
    *,
    allow_previous: bool,
) -> OverlayLookupResult:
    """Resolve frame-keyed annotations using actual video timestamps for their age."""
    requested_time = int(frame_id.timestamp_monotime_us)
    sample = sequence_sample(
        samples.sequences, frame_id.sequence_id, requested_time, timeline, allow_previous=allow_previous
    )
    sequence, sample_time = sample if sample is not None else (None, None)
    scene = load_scene(samples.sequence_to_timestamp[sequence]) if sequence is not None else None
    match_type: OverlayLookupMatchType = "missing"
    if scene is not None:
        match_type = "sequence" if sequence == frame_id.sequence_id else "retained"
    result = _lookup_result(
        samples,
        scene=scene,
        requested_timestamp_us=requested_time,
        matched_timestamp_us=sample_time if scene is not None else None,
        requested_sequence_id=frame_id.sequence_id,
        matched_sequence_id=sequence if scene is not None else None,
        match_type=match_type,
        alignment_basis="sequence",
    )
    stats.record(requested_time, stats.resolved if scene is not None else stats.misses)
    return result


def _lookup_result(
    samples: OverlaySamples,
    *,
    scene: Scene | None,
    requested_timestamp_us: int,
    matched_timestamp_us: int | None,
    requested_sequence_id: int | None,
    matched_sequence_id: int | None,
    match_type: OverlayLookupMatchType,
    alignment_basis: OverlayAlignmentBasis = "timestamp",
) -> OverlayLookupResult:
    """Return a lookup result using the samples' timestamp policy."""
    return OverlayLookupResult(
        scene=scene,
        requested_timestamp_us=requested_timestamp_us,
        matched_timestamp_us=matched_timestamp_us,
        match_type=match_type,
        timestamp_fallback_policy=samples.policy,
        requested_sequence_id=requested_sequence_id,
        matched_sequence_id=matched_sequence_id,
        alignment_basis=alignment_basis,
    )
