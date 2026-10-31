"""Warm the next playlist entry's on-disk caches in a short-lived child process.

The child never opens a `FileFrameSource`: that would take a share of the frame cache pool away from the video that is
playing. It only writes the frame index, video metadata and overlay caches that opening the entry reads. Caches are
written to a temporary file and then replaced, so killing the child at any moment leaves nothing loadable but complete
files. Cancelling kills the child's whole process tree (ffprobe and ffmpeg included) and, once it has exited, deletes
the temporary files it left; they are named with a tag unique to the preparation (`core/atomic_file.py`).
"""

from __future__ import annotations

import os
import pickle
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ax_devil.core.atomic_file import TEMPORARY_TAG_ENV
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import ImageSequenceConfig
from ax_devil.modules.data_sources.file_frame_source import warm_file_caches
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.offline_entry_media import overlay_provider_factory, require_seekable_video
from ax_devil.modules.workspace.core import FileOverlaySourceSpec, PlaylistEntry, SeekableVideoContent

logger = get_logger(__name__)

_CHILD_COMMAND = [sys.executable, "-I", "-m", "ax_devil.modules.video_viewer.entry_preparation"]
_KILL_TIMEOUT_S = 10
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


def entry_preparation(entry: PlaylistEntry, lane_indices: tuple[int, ...]) -> EntryPreparation | None:
    """Describe the caches opening *entry*'s lanes at *lane_indices* would build, or None when there are no lanes."""
    # Lanes showing the same video share one source, exactly as `open_entry_media` shares them.
    videos: dict[tuple[SeekableVideoContent, ImageSequenceConfig | None], tuple[str, ImageSequenceConfig | None]] = {}
    for lane_index in lane_indices:
        video = require_seekable_video(entry.lanes[lane_index].video)
        image_sequence_config = video.source_spec.image_sequence_config
        videos.setdefault((video, image_sequence_config), (str(video.source_spec.path), image_sequence_config))
    if not videos:
        return None
    config_manager = ConfigManager()
    return EntryPreparation(
        videos=tuple(videos.values()),
        overlays=overlay_specs(entry, lane_indices),
        config_path=config_manager.config_path,
        storage=config_manager.get("storage", {}),
    )


@dataclass(frozen=True)
class _Running:
    """The one process warming caches for the next entry, with the pickled request it was started for."""

    payload: bytes
    process: subprocess.Popen[bytes]


_current: _Running | None = None


def _cancel(running: _Running) -> None:
    """Kill *running*'s whole process tree and forget it, so the same request starts a new one; never waits.

    The ffprobe and ffmpeg runs of `VideoAnalyzer` are descendants of the child, so killing only the child would leave
    them running. Its waiter thread reaps it and deletes the temporary files it left.
    """
    global _current
    if _current is running:
        _current = None
    process = running.process
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # taskkill takes a moment, so it runs off the GUI thread.
            threading.Thread(target=_taskkill, args=(process.pid,), name="entry-preparation-kill", daemon=True).start()
        else:
            os.killpg(process.pid, signal.SIGKILL)  # The child leads its own session, see `prepare_entry`.
    except OSError as exc:
        logger.debug(f"Entry preparation process {process.pid} could not be killed: {exc}")


def _taskkill(pid: int) -> None:
    """Kill process *pid* and its descendants on Windows."""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=_KILL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug(f"taskkill of entry preparation process {pid} failed: {exc}")


def _deliver_and_reap(process: subprocess.Popen[bytes], payload: bytes, tag: str, cache_dir: Path | None) -> None:
    """Send *payload* to *process*, reap it when it exits, and delete the temporary files it left in *cache_dir*.

    Runs on a daemon thread and never touches Qt. A killed process cannot clean up after itself, and once it has
    exited nothing writes the files named with its *tag* any more.
    """
    try:
        if process.stdin is not None:
            with process.stdin:
                process.stdin.write(payload)
    except OSError as exc:  # The process was killed or failed before reading its request.
        logger.debug(f"Entry preparation process {process.pid} did not take its request: {exc}")
    returncode = process.wait()
    if returncode == 0 or cache_dir is None:
        return
    logger.debug(f"Entry preparation process {process.pid} exited with code {returncode}")
    try:
        for leftover in cache_dir.rglob(f"*.{tag}.tmp"):
            leftover.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug(f"Entry preparation could not remove its temporary files: {exc}")


def prepare_entry(request: EntryPreparation) -> Callable[[], None]:
    """Start warming *request*'s caches in a child process, replacing any other preparation; GUI thread only.

    Never waits and never fails: problems are logged at debug level and the entry simply opens without a head start.
    The returned function kills this preparation's process tree if it is still running.
    """
    global _current
    try:
        payload = pickle.dumps(request)
    except Exception as exc:
        logger.debug(f"Entry preparation could not describe its request: {exc}")
        return lambda: None
    if _current is not None:
        previous = _current
        if previous.payload == payload and previous.process.poll() in (None, 0):
            return lambda: _cancel(previous)
        _cancel(previous)
    tag = uuid.uuid4().hex[:12]
    try:
        process = subprocess.Popen(
            _CHILD_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, TEMPORARY_TAG_ENV: tag},
            start_new_session=sys.platform != "win32",  # Its own process group, so `_cancel` can kill the whole tree.
        )
    except OSError as exc:
        logger.debug(f"Entry preparation could not start: {exc}")
        return lambda: None
    running = _current = _Running(payload, process)
    logger.debug(
        f"Preparing next entry in process {process.pid}: "
        f"{len(request.videos)} video(s), {len(request.overlays)} overlay(s)"
    )
    cache_dir = request.storage.get("cache_dir")
    threading.Thread(
        target=_deliver_and_reap,
        args=(process, payload, tag, Path(cache_dir).expanduser() if cache_dir else None),
        name="entry-preparation",
        daemon=True,
    ).start()
    return lambda: _cancel(running)


def main() -> None:
    """Child entry point: read an `EntryPreparation` from stdin and warm its caches; the exit code is the result."""
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
