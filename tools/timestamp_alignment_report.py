"""Generate merged video/overlay timestamp alignment TSV reports."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.data_sources.file_frame_source import FileFrameSource
from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
from ax_devil.modules.data_sources.timing_reports import TimestampTimeline
from ax_devil.modules.plugin_system import ApplicationPluginLoader, get_file_decoder_factory
from ax_devil.modules.settings.logging_config import get_logger, setup_logging
from ax_devil.modules.synchronization.timestamp_matching import (
    TimestampFallbackPolicy,
    TimestampMatchResult,
    count_tolerated_past_overlay_timestamps,
    find_matching_overlay_timestamp,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TimelineSummary:
    """Summary counts for a merged video/overlay timestamp timeline."""

    video_frames: int
    overlay_frames: int
    merged_rows: int
    exact_matches: int
    video_only: int
    overlay_only: int
    tolerated_past_matches: int
    tolerance_us: int


@dataclass(frozen=True, slots=True)
class ReportTimeline:
    """Prepared timestamp timeline data for report row rendering."""

    timeline: TimestampTimeline
    index_by_timestamp: dict[int, int]
    timestamp_set: set[int]

    @classmethod
    def from_values(cls, timestamps: tuple[int, ...]) -> "ReportTimeline":
        """Build sorted timeline lookup data from timestamp values."""
        timeline = TimestampTimeline.from_values(timestamps)
        return cls(
            timeline=timeline,
            index_by_timestamp={timestamp_us: index for index, timestamp_us in enumerate(timeline.timestamps_us)},
            timestamp_set=timeline.timestamp_set,
        )

    def nearest_columns(self, timestamp_us: int) -> tuple[str, str, str]:
        """Return nearest timestamp columns for a row."""
        nearest = self.timeline.nearest(timestamp_us)
        if nearest is None:
            return "", "", ""
        nearest_index, nearest_timestamp = nearest
        return str(nearest_index), str(nearest_timestamp), str(nearest_timestamp - timestamp_us)

    def delta_before_text(self, timestamp_us: int) -> str:
        """Return adjacent timestamp delta before ``timestamp_us`` when present."""
        index = self.index_by_timestamp.get(timestamp_us)
        if index is None or index == 0:
            return ""
        return str(self.timeline.timestamps_us[index] - self.timeline.timestamps_us[index - 1])


def main(argv: Sequence[str] | None = None) -> int:
    """Generate a TSV timestamp alignment report."""
    parser = argparse.ArgumentParser(description="Generate merged video/overlay timestamp alignment TSV reports.")
    parser.add_argument("--video", required=True, type=Path, help="Video file to decode timestamps from.")
    parser.add_argument("--overlay", required=True, type=Path, help="Overlay file to inspect.")
    parser.add_argument("--handler", required=True, help="Overlay decoder handler type, such as ADF_BETA_FRAME.")
    parser.add_argument("--output", required=True, type=Path, help="Output TSV path.")
    parser.add_argument(
        "--tolerance-us",
        type=_non_negative_int,
        required=True,
        help="At-or-before timestamp tolerance used for policy columns.",
    )
    args = parser.parse_args(argv)

    setup_logging(console_only=True)
    ApplicationPluginLoader.load_all()

    video_timestamps = _load_video_timestamps(args.video)
    overlay_timestamps = _load_overlay_timestamps(args.overlay, args.handler)
    summary, lines = build_tsv_report(
        video_path=args.video,
        overlay_path=args.overlay,
        handler_type=args.handler,
        video_timestamps=video_timestamps,
        overlay_timestamps=overlay_timestamps,
        tolerance_us=args.tolerance_us,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        f"Wrote {args.output} with {summary.merged_rows} rows: {summary.exact_matches} exact, "
        f"{summary.tolerated_past_matches} tolerated, {summary.overlay_only} overlay-only."
    )
    return 0


def build_tsv_report(
    *,
    video_path: Path,
    overlay_path: Path,
    handler_type: str,
    video_timestamps: tuple[int, ...],
    overlay_timestamps: tuple[int, ...],
    tolerance_us: int,
) -> tuple[TimelineSummary, list[str]]:
    """Build a TSV timestamp report from prepared timestamp timelines."""
    video_timeline = ReportTimeline.from_values(video_timestamps)
    overlay_timeline = ReportTimeline.from_values(overlay_timestamps)
    sorted_video_timestamps = video_timeline.timeline.timestamps_us
    sorted_overlay_timestamps = overlay_timeline.timeline.timestamps_us
    policy = TimestampFallbackPolicy(tolerance_us=tolerance_us)
    video_set = video_timeline.timestamp_set
    overlay_set = overlay_timeline.timestamp_set
    all_timestamps = sorted(video_set | overlay_set)
    exact_matches = len(video_set & overlay_set)
    video_only = len(video_set - overlay_set)
    overlay_only = len(overlay_set - video_set)
    tolerated_past_matches = count_tolerated_past_overlay_timestamps(
        video_timestamps_us=sorted_video_timestamps,
        sorted_overlay_timestamps_us=sorted_overlay_timestamps,
        policy=policy,
    )
    summary = TimelineSummary(
        video_frames=len(sorted_video_timestamps),
        overlay_frames=len(sorted_overlay_timestamps),
        merged_rows=len(all_timestamps),
        exact_matches=exact_matches,
        video_only=video_only,
        overlay_only=overlay_only,
        tolerated_past_matches=tolerated_past_matches,
        tolerance_us=tolerance_us,
    )

    lines = _build_header(
        video_path=video_path,
        overlay_path=overlay_path,
        handler_type=handler_type,
        video_timeline=video_timeline.timeline,
        overlay_timeline=overlay_timeline.timeline,
        summary=summary,
    )
    for timestamp_us in all_timestamps:
        lines.append(
            _build_row(
                timestamp_us=timestamp_us,
                video_timeline=video_timeline,
                overlay_timeline=overlay_timeline,
                policy=policy,
            )
        )
    return summary, lines


def _load_video_timestamps(video_path: Path) -> tuple[int, ...]:
    source = FileFrameSource(str(video_path))
    try:
        return tuple(source.get_frame_timestamps_us())
    finally:
        source.stop()
        source.wait()


def _load_overlay_timestamps(overlay_path: Path, handler_type: str) -> tuple[int, ...]:
    overlay = FileOverlaySource(overlay_path, get_file_decoder_factory(handler_type), handler_type)
    try:
        return tuple(sorted(overlay.data_provider.get_available_frames()))
    finally:
        overlay.close()


def _build_header(
    *,
    video_path: Path,
    overlay_path: Path,
    handler_type: str,
    video_timeline: TimestampTimeline,
    overlay_timeline: TimestampTimeline,
    summary: TimelineSummary,
) -> list[str]:
    return [
        "# timestamp alignment report",
        f"# video={video_path}",
        f"# overlay={overlay_path}",
        f"# handler={handler_type}",
        "# timestamp units are microseconds since first decoded video frame",
        f"# video_frames={summary.video_frames} overlay_frames={summary.overlay_frames} "
        f"merged_rows={summary.merged_rows}",
        f"# exact_matches={summary.exact_matches} video_only={summary.video_only} overlay_only={summary.overlay_only}",
        f"# tolerated_past_matches={summary.tolerated_past_matches} tolerance_us={summary.tolerance_us}",
        f"# video_delta_modes={video_timeline.delta_modes_us(8)}",
        f"# overlay_delta_modes={overlay_timeline.delta_modes_us(8)}",
        "# row_type: EXACT means same timestamp exists in both video and overlay",
        "# policy columns use: exact first, else overlay timestamp at or before video timestamp within tolerance",
        "\t".join(
            [
                "timestamp_us",
                "timestamp_s",
                "row_type",
                "video_index",
                "overlay_index",
                "video_delta_before_us",
                "overlay_delta_before_us",
                "nearest_video_index",
                "nearest_video_timestamp_us",
                "nearest_video_offset_us",
                "nearest_overlay_index",
                "nearest_overlay_timestamp_us",
                "nearest_overlay_offset_us",
                "policy_match_type",
                "policy_overlay_index",
                "policy_overlay_timestamp_us",
                "policy_overlay_offset_us",
            ]
        ),
    ]


def _build_row(
    *,
    timestamp_us: int,
    video_timeline: ReportTimeline,
    overlay_timeline: ReportTimeline,
    policy: TimestampFallbackPolicy,
) -> str:
    in_video = timestamp_us in video_timeline.timestamp_set
    in_overlay = timestamp_us in overlay_timeline.timestamp_set
    if in_video and in_overlay:
        row_type = "EXACT"
    elif in_video:
        row_type = "VIDEO_ONLY"
    else:
        row_type = "OVERLAY_ONLY"

    nearest_video = ("", "", "") if in_video else video_timeline.nearest_columns(timestamp_us)
    nearest_overlay = ("", "", "") if in_overlay else overlay_timeline.nearest_columns(timestamp_us)
    policy_match = (
        find_matching_overlay_timestamp(
            sorted_overlay_timestamps_us=overlay_timeline.timeline.timestamps_us,
            video_timestamp_us=timestamp_us,
            policy=policy,
        )
        if in_video
        else TimestampMatchResult(None, None, None, "missing")
    )
    policy_match_type = policy_match.match_type if in_video else ""

    return "\t".join(
        [
            str(timestamp_us),
            f"{timestamp_us / 1_000_000:.6f}",
            row_type,
            str(video_timeline.index_by_timestamp[timestamp_us]) if in_video else "",
            str(overlay_timeline.index_by_timestamp[timestamp_us]) if in_overlay else "",
            video_timeline.delta_before_text(timestamp_us),
            overlay_timeline.delta_before_text(timestamp_us),
            *nearest_video,
            *nearest_overlay,
            policy_match_type,
            str(policy_match.overlay_index) if policy_match.overlay_index is not None else "",
            str(policy_match.overlay_timestamp_us) if policy_match.overlay_timestamp_us is not None else "",
            str(policy_match.offset_us) if policy_match.offset_us is not None else "",
        ]
    )


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
