"""MOT Challenge dataset discovery and playlist construction."""

from __future__ import annotations

import configparser
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder import ImageSequenceConfig
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace import (
    EntryLane,
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    SeekableVideoContent,
    create_entry_lane,
)

logger = get_logger(__name__)

MOT_FILE_HANDLER = "MOT_FILE"
MOT_DETECTOR_SUFFIXES = frozenset({"DPM", "FRCNN", "SDP"})


@dataclass(frozen=True, slots=True)
class SequenceInfo:
    """Metadata for a single MOT Challenge sequence parsed from ``seqinfo.ini``."""

    name: str
    path: Path
    fps: float
    width: int
    height: int
    frame_count: int
    im_dir: str
    im_ext: str


def parse_seqinfo(ini_path: Path) -> dict[str, str]:
    """Parse a MOT Challenge ``seqinfo.ini`` file and return its ``[Sequence]`` section as a dict.

    Note: ``configparser`` lowercases all keys, so callers should use lowercase
    key names (e.g. ``framerate`` instead of ``frameRate``).
    """
    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")
    if not parser.has_section("Sequence"):
        raise ValueError(f"Missing [Sequence] section in {ini_path}")
    return dict(parser["Sequence"])


def discover_sequences(root: Path) -> list[SequenceInfo]:
    """Scan *root* for subdirectories containing ``seqinfo.ini`` and return a sorted list of sequences."""
    if not root.is_dir():
        raise FileNotFoundError(f"MOT root directory does not exist: {root}")

    sequences: list[SequenceInfo] = []
    for child in _sequence_candidate_dirs(root):
        ini_path = child / "seqinfo.ini"
        if not ini_path.is_file():
            continue
        try:
            raw = parse_seqinfo(ini_path)
            seq = SequenceInfo(
                name=raw.get("name", child.name),
                path=child,
                fps=float(raw["framerate"]),
                width=int(raw["imwidth"]),
                height=int(raw["imheight"]),
                frame_count=int(raw["seqlength"]),
                im_dir=raw.get("imdir", "img1"),
                im_ext=raw.get("imext", ".jpg"),
            )
            sequences.append(seq)
            logger.debug(f"Discovered MOT sequence: {seq.name} ({seq.frame_count} frames)")
        except (KeyError, ValueError) as exc:
            logger.warning(f"Skipping {child.name}: failed to parse seqinfo.ini ({exc})")
    return sequences


def _sequence_candidate_dirs(root: Path) -> list[Path]:
    candidates = list(root.iterdir())
    for split_name in ("train", "test"):
        split_dir = root / split_name
        if split_dir.is_dir():
            candidates.extend(split_dir.iterdir())
    return sorted({path for path in candidates if path.is_dir()})


def build_playlist_contents(sequences: list[SequenceInfo]) -> list[PlaylistContent]:
    """Build canonical workspace playlist contents from MOT Challenge sequences.

    Detector variants such as ``MOT17-01-DPM/FRCNN/SDP`` are grouped into one
    PlaylistEntry so the shared video can be compared with multiple overlays
    side-by-side. Ground-truth overlays are deduplicated within a grouped
    entry because they are identical across detector variants.
    """
    entries: list[PlaylistEntry] = []

    for display_name, grouped_sequences in _group_sequences(sequences):
        video_seq = grouped_sequences[0]
        overlays = _build_group_overlay_specs(grouped_sequences)
        video = _build_video_content(video_seq, display_name)
        lanes = _build_group_lanes(video, overlays)
        entries.append(PlaylistEntry(lanes=tuple(lanes), default_considered=True))

    if not entries:
        return []

    return [
        PlaylistContent(
            display_name="MOT Challenge",
            entries=tuple(entries),
            metadata={"resolver": "MOT Challenge", "sequence_groups": len(entries)},
        )
    ]


def _build_video_content(
    seq: SequenceInfo,
    display_name: str | None = None,
) -> SeekableVideoContent:
    video_path = seq.path / seq.im_dir / f"%06d{seq.im_ext}"
    image_seq_config = ImageSequenceConfig(
        fps=seq.fps,
        width=seq.width,
        height=seq.height,
        total_frames=seq.frame_count,
        start_number=1,
    )
    return SeekableVideoContent(
        display_name=display_name or seq.name,
        source_spec=FileVideoSourceSpec(path=video_path, image_sequence_config=image_seq_config),
    )


def _group_sequences(sequences: list[SequenceInfo]) -> list[tuple[str, list[SequenceInfo]]]:
    """Group detector variants that share the same underlying MOT video."""
    grouped: OrderedDict[str, list[SequenceInfo]] = OrderedDict()
    for seq in sequences:
        grouped.setdefault(_base_sequence_name(seq.name), []).append(seq)
    return list(grouped.items())


def _base_sequence_name(name: str) -> str:
    """Collapse known detector suffixes so MOT17 detector variants group together."""
    prefix, separator, suffix = name.rpartition("-")
    if separator and suffix.upper() in MOT_DETECTOR_SUFFIXES:
        return prefix
    return name


def _build_group_overlay_specs(sequences: list[SequenceInfo]) -> list[OverlayContent]:
    """Create ordered overlay specs for a grouped MOT sequence entry."""
    overlay_specs: list[OverlayContent] = []
    gt_added = False

    for seq in sequences:
        det_path = seq.path / "det" / "det.txt"
        if det_path.is_file():
            overlay_specs.append(
                OverlayContent(
                    display_name=_detector_label(seq.name),
                    source_spec=FileOverlaySourceSpec(
                        path=det_path,
                        handler_type=MOT_FILE_HANDLER,
                        decoder_kwargs={"width": seq.width, "height": seq.height},
                    ),
                )
            )

        gt_path = seq.path / "gt" / "gt.txt"
        if gt_path.is_file() and not gt_added:
            overlay_specs.append(
                OverlayContent(
                    display_name="GT",
                    source_spec=FileOverlaySourceSpec(
                        path=gt_path,
                        handler_type=MOT_FILE_HANDLER,
                        decoder_kwargs={"width": seq.width, "height": seq.height},
                    ),
                )
            )
            gt_added = True

    return overlay_specs


def _build_group_lanes(video: SeekableVideoContent, overlays: list[OverlayContent]) -> list[EntryLane]:
    """Build explicit entry lanes for a shared MOT video and its overlays."""
    if not overlays:
        return [create_entry_lane(video, default_considered=True)]
    return [create_entry_lane(video, default_considered=True, overlay=overlay) for overlay in overlays]


def _detector_label(name: str) -> str:
    """Return a human-readable overlay label for detector-specific MOT sequences."""
    prefix, separator, suffix = name.rpartition("-")
    if separator and prefix and suffix.upper() in MOT_DETECTOR_SUFFIXES:
        return suffix.upper()
    return "DET"
