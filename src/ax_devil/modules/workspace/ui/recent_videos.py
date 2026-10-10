"""Recently opened video files, kept for the welcome screen."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core import OverlayFile, VideoItem

logger = get_logger(__name__)

RECENT_VIDEOS_LIMIT = 5


class RecentVideos:
    """Persist the most recently opened Video Items, newest first, in one JSON file.

    Entries keep the item's files and label, not its id; each entry read back is a new item.
    """

    def __init__(self, path: Path, limit: int = RECENT_VIDEOS_LIMIT) -> None:
        self._path = path
        self._limit = limit

    def entries(self) -> tuple[VideoItem, ...]:
        """Return remembered items whose video file still exists, newest first."""
        return tuple(entry for entry in self._load() if entry.video.is_file())

    def record(self, item: VideoItem) -> None:
        """Put *item* first, drop entries for this or deleted videos, and keep at most the limit."""
        item = replace(
            item,
            video=item.video.resolve(),
            overlays=tuple(OverlayFile(overlay.path.resolve(), overlay.decoder) for overlay in item.overlays),
        )
        older = (entry for entry in self.entries() if entry.video != item.video)
        entries = [item, *older][: self._limit]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([_to_json(entry) for entry in entries], indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(f"Could not save recent videos to {self._path}: {exc}")

    def _load(self) -> list[VideoItem]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"Ignoring unreadable recent videos file {self._path}: {exc}")
            return []
        entries = []
        for item in raw if isinstance(raw, list) else []:
            try:
                entries.append(_from_json(item))
            except (TypeError, KeyError, AttributeError) as exc:
                logger.warning(f"Skipping unreadable recent video entry in {self._path}: {exc}")
        return entries


def default_recent_videos() -> RecentVideos:
    """Return the recent-video list stored in the configured application storage directory."""
    storage = ConfigManager().get("storage")
    return RecentVideos(Path(storage["base_dir"]) / "recent-videos.json")


def _to_json(item: VideoItem) -> dict[str, Any]:
    return {
        "label": item.label,
        "video": str(item.video),
        "overlays": [{"path": str(overlay.path), "decoder": overlay.decoder} for overlay in item.overlays],
    }


def _from_json(entry: dict[str, Any]) -> VideoItem:
    return VideoItem(
        label=_text(entry, "label"),
        video=Path(_text(entry, "video")),
        overlays=tuple(
            OverlayFile(Path(_text(overlay, "path")), _text(overlay, "decoder")) for overlay in entry["overlays"]
        ),
    )


def _text(entry: dict[str, Any], key: str) -> str:
    """Return the text stored under *key*, rejecting any other stored type."""
    value = entry[key]
    if not isinstance(value, str):
        raise TypeError(f"Recent video field {key} must be text, got {type(value).__name__}")
    return value
