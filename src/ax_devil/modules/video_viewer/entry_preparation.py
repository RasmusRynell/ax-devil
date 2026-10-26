"""Warm the next playlist entry's on-disk caches in a short-lived child process.

The child never opens a `FileFrameSource`: that would take a share of the frame cache pool away from the video that is
playing. It only writes the frame index, video metadata and overlay caches that opening the entry reads. Caches are
written to a temporary file and then replaced, so terminating the child at any moment leaves nothing loadable but
complete files.
"""

from __future__ import annotations

import os
import pickle
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.data_sources.file_frame_source import warm_file_caches
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.offline_entry_media import overlay_provider_factory, require_seekable_video
from ax_devil.modules.workspace.core import FileOverlaySourceSpec, PlaylistEntry, SeekableVideoContent

logger = get_logger(__name__)

_MODULE = "ax_devil.modules.video_viewer.entry_preparation"
_CHILD_NICE = 10
_CHILD_DECODER_THREADS = 2


@dataclass(frozen=True)
class EntryPreparation:
    """What the child process needs to warm one entry's caches, as plain picklable data.

    *config_path* and *storage* are the parent's, so the child writes caches where the parent will look for them.
    """

    videos: tuple[tuple[str, ImageSequenceConfig | None], ...]
    overlays: tuple[FileOverlaySourceSpec, ...]
    config_path: Path
    storage: dict[str, Any]


def overlay_specs(entry: PlaylistEntry, lane_indices: tuple[int, ...]) -> tuple[FileOverlaySourceSpec, ...]:
    """Return the distinct file overlay sources of *entry*'s lanes at *lane_indices*."""
    specs: list[FileOverlaySourceSpec] = []
    for lane_index in lane_indices:
        overlay = entry.lanes[lane_index].overlay
        spec = overlay.source_spec if overlay is not None else None
        if isinstance(spec, FileOverlaySourceSpec) and not any(_same_overlay(known, spec) for known in specs):
            specs.append(spec)
    return tuple(specs)


def _same_overlay(first: FileOverlaySourceSpec, second: FileOverlaySourceSpec) -> bool:
    """Return whether both specs open the same file the same way; spec equality ignores the decoder options."""
    return first == second and first.decoder_kwargs == second.decoder_kwargs


def entry_preparation(
    entry: PlaylistEntry, lane_indices: tuple[int, ...], playing: tuple[FileOverlaySourceSpec, ...]
) -> EntryPreparation | None:
    """Describe the caches opening *entry*'s lanes at *lane_indices* would build, or None when there are no lanes.

    An overlay cache is named after its file alone, so one that the *playing* overlays also read, but with other decoder
    options, is left out: replacing it would change what the playing provider reads from it.
    """
    # Lanes showing the same video share one source, exactly as `open_entry_media` shares them.
    videos: dict[tuple[SeekableVideoContent, ImageSequenceConfig | None], tuple[str, ImageSequenceConfig | None]] = {}
    for lane_index in lane_indices:
        video = require_seekable_video(entry.lanes[lane_index].video)
        image_sequence_config = video.source_spec.image_sequence_config
        videos.setdefault((video, image_sequence_config), (str(video.source_spec.path), image_sequence_config))
    if not videos:
        return None
    overlays = tuple(
        spec
        for spec in overlay_specs(entry, lane_indices)
        if all(_same_overlay(spec, shown) for shown in playing if shown.path == spec.path)
    )
    config_manager = ConfigManager()
    return EntryPreparation(
        videos=tuple(videos.values()),
        overlays=overlays,
        config_path=config_manager.config_path,
        storage=config_manager.get("storage", {}),
    )


# The one process warming caches for the next entry, with the pickled request that identifies what it was started for.
_current: tuple[bytes, subprocess.Popen[bytes]] | None = None


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Ask *process* to stop and forget it, so the same request starts a new one; its waiter thread reaps it."""
    global _current
    if _current is not None and _current[1] is process:
        _current = None
    try:
        process.terminate()
    except OSError as exc:
        logger.debug(f"Entry preparation process {process.pid} could not be terminated: {exc}")


def _deliver_and_reap(process: subprocess.Popen[bytes], payload: bytes) -> None:
    """Send *payload* to *process* and reap it when it exits. Runs on a daemon thread and never touches Qt."""
    try:
        if process.stdin is not None:
            with process.stdin:
                process.stdin.write(payload)
    except OSError as exc:  # The process was terminated or failed before reading its request.
        logger.debug(f"Entry preparation process {process.pid} did not take its request: {exc}")
    returncode = process.wait()
    if returncode != 0:
        logger.debug(f"Entry preparation process {process.pid} exited with code {returncode}")


def prepare_entry(request: EntryPreparation) -> Callable[[], None]:
    """Start warming *request*'s caches in a child process, replacing any other preparation; GUI thread only.

    Never waits and never fails: problems are logged at debug level and the entry simply opens without a head start.
    The returned function terminates this preparation if it is still running.
    """
    global _current
    try:
        payload = pickle.dumps(request)
    except Exception as exc:
        logger.debug(f"Entry preparation could not describe its request: {exc}")
        return lambda: None
    if _current is not None:
        running_payload, running = _current
        if running_payload == payload and running.poll() in (None, 0):
            return lambda: _terminate(running)
        _terminate(running)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-m", _MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.debug(f"Entry preparation could not start: {exc}")
        return lambda: None
    _current = (payload, process)
    logger.debug(
        f"Preparing next entry in process {process.pid}: "
        f"{len(request.videos)} video(s), {len(request.overlays)} overlay(s)"
    )
    threading.Thread(target=_deliver_and_reap, args=(process, payload), name="entry-preparation", daemon=True).start()
    return lambda: _terminate(process)


def _exit_on_sigterm(_signal_number: int, _frame: FrameType | None) -> None:
    """Unwind as an exit, so `finally` blocks remove temporary cache files."""
    raise SystemExit(1)


def main() -> None:
    """Child entry point: read an `EntryPreparation` from stdin and warm its caches; the exit code is the result."""
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    if hasattr(os, "nice"):  # Windows has no nice; there the child keeps its normal priority.
        os.nice(_CHILD_NICE)
    try:
        request: EntryPreparation = pickle.load(sys.stdin.buffer)
        config_manager = ConfigManager()
        config_manager.set_config_path(request.config_path, create_if_missing=False)
        config_manager.use_storage(request.storage)
        for path, image_sequence_config in request.videos:
            warm_file_caches(path, image_sequence_config, thread_count=_CHILD_DECODER_THREADS)
        if request.overlays:
            ApplicationPluginLoader.load_all()
            for spec in request.overlays:
                overlay_provider_factory(spec)(spec.path).close()
    except Exception:
        logger.exception("Entry preparation failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
