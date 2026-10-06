"""On-demand Python stack sampling with a disk-backed recording history."""

import json
import os
import sys
import tempfile
from pathlib import Path
from threading import Event, Thread, get_ident
from threading import enumerate as enumerate_threads
from time import monotonic
from types import FrameType
from typing import BinaryIO

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class TraceRecorder:
    """Sample all Python threads at a target 100 Hz without overwriting old samples."""

    interval_seconds = 0.01

    def __init__(self) -> None:
        self.recording = False
        self.failure: str | None = None
        self.sample_count = 0
        self._stop = Event()
        self._worker: Thread | None = None
        self._spool: BinaryIO | None = None
        self._frames: dict[str, int] = {}
        self._stacks: dict[tuple[int, ...], int] = {}
        self._threads: dict[str, str] = {}
        self._active_threads: dict[int, tuple[Thread | None, str]] = {}
        self._started_at = 0.0
        self._ended_at = 0.0

    @property
    def has_trace(self) -> bool:
        """Whether stopped samples are available to save."""
        return self._spool is not None and not self.recording

    def start(self) -> None:
        """Start a fresh recording with a dedicated sampler thread and temporary file."""
        if self.recording:
            raise RuntimeError("A recording is already running.")
        # Allocate first so a failed allocation does not discard the last recording.
        spool = tempfile.TemporaryFile(mode="w+b", buffering=0)
        self.cleanup()
        self._spool = spool
        self.failure = None
        self.sample_count = 0
        self._stop.clear()
        self._started_at = monotonic()
        self._worker = Thread(target=self._run, name="Performance sampler", daemon=True)
        self.recording = True
        try:
            self._worker.start()
        except Exception:
            self._worker = None
            self.recording = False
            self.cleanup()
            raise
        logger.info("Performance stack sampling started (target 100 Hz)")

    def _stack_id(self, frame: FrameType) -> int:
        stack: list[int] = []
        current: FrameType | None = frame
        while current is not None:
            code = current.f_code
            name = f"{code.co_name} ({code.co_filename}:{code.co_firstlineno})"
            stack.append(self._frames.setdefault(name, len(self._frames)))
            current = current.f_back
        key = tuple(reversed(stack))
        return self._stacks.setdefault(key, len(self._stacks))

    def _sample(self) -> None:
        assert self._spool is not None
        timestamp_ms = (monotonic() - self._started_at) * 1000
        threads = {thread.ident: thread for thread in enumerate_threads()}
        frames = sys._current_frames()
        self._active_threads = {key: value for key, value in self._active_threads.items() if key in frames}
        own_id = get_ident()
        stacks: list[tuple[str, int]] = []
        for thread_id, frame in frames.items():
            if thread_id == own_id:
                continue
            thread = threads.get(thread_id)
            previous = self._active_threads.get(thread_id)
            if previous is None or previous[0] is not thread:
                key = str(len(self._threads))
                self._active_threads[thread_id] = (thread, key)
                self._threads[key] = thread.name if thread is not None else f"Thread {thread_id}"
            else:
                key = previous[1]
            stacks.append((key, self._stack_id(frame)))
        # Unbuffered writes retain completed records even if a later write fails.
        record = f"{json.dumps([timestamp_ms, stacks], separators=(',', ':'))}\n".encode("utf-8")
        if self._spool.write(record) != len(record):
            raise OSError("The temporary recording could not be written completely.")
        self.sample_count += 1

    def _run(self) -> None:
        try:
            deadline = monotonic()
            while not self._stop.is_set():
                self._sample()
                deadline += self.interval_seconds
                delay = deadline - monotonic()
                if delay < 0:
                    deadline = monotonic() + self.interval_seconds
                    delay = self.interval_seconds
                self._stop.wait(delay)
        except Exception as error:
            self.failure = str(error)
            logger.exception(f"Performance sampling stopped early: {error}")
        finally:
            self._ended_at = monotonic()

    def stop(self) -> None:
        """Stop and join the sampler before exporting or releasing its history."""
        if not self.recording:
            return
        self._stop.set()
        if self._worker is not None:
            self._worker.join()
            self._worker = None
        self.recording = False
        logger.info(f"Performance sampling stopped: {self.sample_count} snapshots retained")

    def save(self, path: Path) -> None:
        """Atomically export all retained snapshots; failed exports remain retryable."""
        if not self.has_trace or self._spool is None:
            raise RuntimeError("Stop a recording before saving it.")
        metadata = {
            "format": "ax-devil-stack-samples",
            "version": 1,
            "interval_ms": self.interval_seconds * 1000,
            "duration_ms": (self._ended_at - self._started_at) * 1000,
            "failure": self.failure,
            "frames": list(self._frames),
            "stacks": list(self._stacks),
            "threads": self._threads,
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
                temporary_path = Path(output.name)
                header = json.dumps(metadata, separators=(",", ":"))
                output.write(f'{header[:-1]},"samples":[\n')
                self._spool.seek(0)
                for index in range(self.sample_count):
                    line = self._spool.readline()
                    if not line.endswith(b"\n"):
                        raise OSError("The temporary recording is incomplete.")
                    if index:
                        output.write(",\n")
                    output.write(line.decode("utf-8").rstrip("\n"))
                output.write("\n]}\n")
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        logger.info(f"Performance samples saved to {path}")

    def cleanup(self) -> None:
        """Stop sampling and release temporary storage and interned stack data."""
        self.stop()
        if self._spool is not None:
            self._spool.close()
            self._spool = None
        self._frames.clear()
        self._stacks.clear()
        self._threads.clear()
        self._active_threads.clear()
