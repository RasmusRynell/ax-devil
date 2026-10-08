"""Generic worker thread for background processing tasks."""

import threading
from abc import ABC, abstractmethod
from enum import Enum

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class WorkerState(Enum):
    """States for background worker threads."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class BaseWorker(ABC):
    """Base class for work execution strategies."""

    @abstractmethod
    def do_work(self) -> bool:
        """Execute one unit of work. Returns True if work was done, False if idle."""
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up resources when worker stops."""
        ...


class FrameWorker:
    """Generic background worker thread with state management."""

    def __init__(
        self,
        work_executor: BaseWorker,
        worker_timeout: float = 5.0,
        thread_name: str = "FrameWorker",
    ) -> None:
        self._work_executor: BaseWorker | None = work_executor
        self._worker_timeout = worker_timeout
        self._thread_name = thread_name

        # Thread management
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._resume_event = threading.Event()
        self._work_available_event = threading.Event()

        # State management
        self._state = WorkerState.STOPPED
        self._state_lock = threading.Lock()

        logger.debug(f"FrameWorker created: id={id(self)}, thread_name={thread_name}")

    @property
    def state(self) -> WorkerState:
        """Get current worker state."""
        with self._state_lock:
            return self._state

    @property
    def is_running(self) -> bool:
        """Check if worker is currently processing."""
        return self.state == WorkerState.RUNNING

    @property
    def is_paused(self) -> bool:
        """Check if worker is currently paused."""
        return self.state == WorkerState.PAUSED

    @property
    def is_stopped(self) -> bool:
        """Check if worker is currently stopped."""
        return self.state == WorkerState.STOPPED

    def start(self) -> None:
        """Start the worker thread."""
        with self._state_lock:
            if self._state != WorkerState.STOPPED:
                logger.debug(f"FrameWorker is already {self._state.value}")
                return

            self._stop_event.clear()
            self._pause_event.clear()
            self._resume_event.set()
            self._work_available_event.clear()
            self._state = WorkerState.RUNNING
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name=self._thread_name)
            self._worker_thread.start()
            logger.debug(f"{self._thread_name} started")

    def pause(self) -> None:
        """Pause the worker thread."""
        with self._state_lock:
            if self._state != WorkerState.RUNNING:
                logger.debug(f"Cannot pause: {self._thread_name} is {self._state.value}")
                return

            self._pause_event.set()
            self._resume_event.clear()
            self._state = WorkerState.PAUSED
            logger.debug(f"{self._thread_name} paused")

    def resume(self) -> None:
        """Resume the worker thread."""
        with self._state_lock:
            if self._state != WorkerState.PAUSED:
                logger.debug(f"Cannot resume: {self._thread_name} is {self._state.value}")
                return

            self._pause_event.clear()
            self._resume_event.set()
            self._state = WorkerState.RUNNING
            logger.debug(f"{self._thread_name} resumed")

    def stop(self) -> None:
        """Stop the worker thread."""
        with self._state_lock:
            if self._state == WorkerState.STOPPED:
                logger.debug(f"{self._thread_name} is already stopped")
                return

            self._stop_event.set()
            self._resume_event.set()
            self._work_available_event.set()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=self._worker_timeout)
            if self._worker_thread.is_alive():
                logger.warning(f"{self._thread_name} did not stop gracefully within timeout")

        with self._state_lock:
            self._state = WorkerState.STOPPED

        logger.debug(f"{self._thread_name} stopped")

    def wait(self, timeout_ms: int = 2000) -> bool:
        """Wait for the worker thread to finish."""
        worker_thread = self._worker_thread
        if worker_thread is None:
            return True
        worker_thread.join(timeout=max(0, timeout_ms) / 1000)
        return not worker_thread.is_alive()

    def signal_work_available(self) -> None:
        """Signal that work is available for processing."""
        self._work_available_event.set()

    def _worker_loop(self) -> None:
        """Main worker thread loop."""
        try:
            while not self._stop_event.is_set():
                # Wait while paused - block until resumed or stopped
                while self._pause_event.is_set() and not self._stop_event.is_set():
                    self._resume_event.wait()

                # Check stop again after potential pause
                if self._stop_event.is_set():
                    break

                # Execute work
                assert self._work_executor is not None, "Work executor must be available during loop"
                work_done = self._work_executor.do_work()

                if not work_done:
                    # No work to do, wait for work to become available
                    # Use timeout to prevent indefinite blocking due to race conditions
                    # This ensures we periodically check for work even if signal is missed
                    self._work_available_event.wait(timeout=0.1)
                    self._work_available_event.clear()

        except Exception as e:
            logger.error(f"{self._thread_name} crashed: {e}")
            with self._state_lock:
                self._state = WorkerState.STOPPED
            raise
        finally:
            # Clean up resources
            try:
                if self._work_executor:
                    self._work_executor.cleanup()
            except Exception as e:
                logger.warning(f"Error during {self._thread_name} cleanup: {e}")

            # Break circular references
            self._work_executor = None
            logger.debug(f"{self._thread_name} stopped and references cleared")
