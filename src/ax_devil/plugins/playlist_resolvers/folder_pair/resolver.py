"""Playlist resolver for matching video and overlay folders by file name."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    PlaylistSettings,
    SeekableVideoContent,
    create_entry_lane,
)

logger = get_logger(__name__)

_COMPRESSION_SUFFIXES = {".bz2", ".gz", ".xz", ".zst"}


@dataclass(frozen=True, slots=True)
class FolderPairMatch:
    """One matched video and overlay file pair."""

    name: str
    video_path: Path
    overlay_path: Path


def resolve_settings(settings: PlaylistSettings) -> list[PlaylistContent]:
    """Return the playlist for ``{"videos_dir": ..., "overlays_dir": ..., "handler_type": ...}`` settings."""
    videos_dir = Path(_required_text(settings, "videos_dir"))
    overlays_dir = Path(_required_text(settings, "overlays_dir"))
    playlists = build_playlist_contents(
        discover_folder_pairs(videos_dir, overlays_dir), _required_text(settings, "handler_type")
    )
    if not playlists:
        raise ValueError(f"No matched video/overlay pairs in '{videos_dir}' and '{overlays_dir}'.")
    return playlists


def _required_text(settings: PlaylistSettings, key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Folder Pair setting '{key}' must be a non-empty string.")
    return value


def discover_folder_pairs(videos_dir: Path, overlays_dir: Path) -> list[FolderPairMatch]:
    """Return files from two folders that share the same suffix-stripped file name."""
    if not videos_dir.is_dir():
        raise FileNotFoundError(f"Video directory does not exist: {videos_dir}")
    if not overlays_dir.is_dir():
        raise FileNotFoundError(f"Overlay directory does not exist: {overlays_dir}")

    videos = _index_files_by_name(videos_dir, "video")
    overlays = _index_files_by_name(overlays_dir, "overlay")
    matches: list[FolderPairMatch] = []

    for name in sorted(videos.keys() & overlays.keys()):
        matches.append(FolderPairMatch(name=name, video_path=videos[name], overlay_path=overlays[name]))

    logger.debug(f"Discovered {len(matches)} folder-pair playlist match(es)")
    return matches


def build_playlist_contents(matches: list[FolderPairMatch], handler_type: str) -> list[PlaylistContent]:
    """Build one playlist from matched folder-pair files and the selected file decoder."""
    if not matches:
        return []

    entries: list[PlaylistEntry] = []
    for match in matches:
        video = SeekableVideoContent(
            display_name=match.name,
            source_spec=FileVideoSourceSpec(path=match.video_path),
        )
        overlay = OverlayContent(
            display_name=match.name,
            source_spec=FileOverlaySourceSpec(path=match.overlay_path, handler_type=handler_type),
        )
        entries.append(
            PlaylistEntry(
                lanes=(create_entry_lane(video, default_considered=True, overlay=overlay),),
                default_considered=True,
                metadata={"name": match.name},
            )
        )

    return [
        PlaylistContent(
            display_name="Folder Pair",
            entries=tuple(entries),
            metadata={"resolver": "Folder Pair", "matches": len(entries)},
        )
    ]


def _index_files_by_name(directory: Path, label: str) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    duplicates: set[str] = set()

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        name = _match_name(path)
        if name in indexed:
            duplicates.add(name)
            continue
        indexed[name] = path

    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"Duplicate {label} file name(s) after suffix stripping: {duplicate_list}")

    return indexed


def _match_name(path: Path) -> str:
    suffixes = path.suffixes
    name = path.name

    if suffixes and suffixes[-1].lower() in _COMPRESSION_SUFFIXES:
        name = name.removesuffix(suffixes[-1])
        suffixes = suffixes[:-1]

    if suffixes:
        name = name.removesuffix(suffixes[-1])

    return name
