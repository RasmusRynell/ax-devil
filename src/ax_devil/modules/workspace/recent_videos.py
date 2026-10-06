"""Recently opened video files, kept for the welcome screen."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.startup_request import VideoFileStartup

logger = get_logger(__name__)

RECENT_VIDEOS_LIMIT = 5


class RecentVideos:
    """Persist the most recently opened video requests, newest first, in one JSON file."""

    def __init__(self, path: Path, limit: int = RECENT_VIDEOS_LIMIT) -> None:
        self._path = path
        self._limit = limit

    def entries(self) -> tuple[VideoFileStartup, ...]:
        """Return remembered requests whose video file still exists, newest first."""
        return tuple(entry for entry in self._load() if entry.video_path.is_file())

    def record(self, request: VideoFileStartup) -> None:
        """Put *request* first, drop entries for this or deleted videos, and keep at most the limit."""
        request = replace(
            request,
            video_path=request.video_path.resolve(),
            overlay_path=request.overlay_path.resolve() if request.overlay_path is not None else None,
        )
        older = (entry for entry in self.entries() if entry.video_path != request.video_path)
        entries = [request, *older][: self._limit]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps([_to_json(entry) for entry in entries], indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(f"Could not save recent videos to {self._path}: {exc}")

    def _load(self) -> list[VideoFileStartup]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return [_from_json(item) for item in raw]
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning(f"Ignoring unreadable recent videos file {self._path}: {exc}")
            return []


def default_recent_videos() -> RecentVideos:
    """Return the recent-video list stored in the configured application storage directory."""
    storage = ConfigManager().get("storage")
    return RecentVideos(Path(storage["base_dir"]) / "recent-videos.json")


def _to_json(entry: VideoFileStartup) -> dict[str, Any]:
    return {
        "video_path": str(entry.video_path),
        "overlay_path": str(entry.overlay_path) if entry.overlay_path is not None else None,
        "handler_type": entry.handler_type,
        "display_name": entry.display_name,
    }


def _from_json(item: dict[str, Any]) -> VideoFileStartup:
    overlay = _optional_text(item, "overlay_path")
    return VideoFileStartup(
        video_path=Path(item["video_path"]),
        overlay_path=Path(overlay) if overlay else None,
        handler_type=_optional_text(item, "handler_type"),
        display_name=_optional_text(item, "display_name"),
    )


def _optional_text(item: dict[str, Any], key: str) -> str | None:
    """Return the text stored under *key*, rejecting any other stored type."""
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"Recent video field {key} must be text, got {type(value).__name__}")
    return value
