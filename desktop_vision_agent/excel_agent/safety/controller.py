"""Stop / pause / resume coordination for the safety layer (Segment 3)."""

from __future__ import annotations

import logging
import queue
import threading
import time
from enum import Enum
from typing import Callable

from ..config import SafetyConfig
from .hotkeys import hotkeys_from_config
from .input_lock import InputLock, InputLockError
from .overlay import StatusOverlay
from .watchdog import Watchdog

logger = logging.getLogger(__name__)


class SafetyError(RuntimeError):
    """The safety controller could not start or change state."""


class SafetyState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class SafetyController:
    """Own the input lock and turn hotkeys into safe state changes.

    Hotkey callbacks run inside the low-level hook, so they only enqueue an
    action. A worker thread performs the actual lock changes.
    """

    def __init__(
        self,
        config: SafetyConfig,
        *,
        lock=None,
        overlay=None,
        watchdog=None,
        on_state: Callable[[SafetyState, str], None] | None = None,
    ) -> None:
        self._hotkeys = hotkeys_from_config(config)
        self._on_state = on_state
        self._actions: queue.Queue[str] = queue.Queue()
        self._state = SafetyState.IDLE
        self._reason = ""
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        self._worker_stop = threading.Event()

        self._lock = (
            lock
            if lock is not None
            else InputLock(self._hotkeys, self._actions.put)
        )
        self._overlay = (
            overlay
            if overlay is not None
            else StatusOverlay({hotkey.action: hotkey.label for hotkey in self._hotkeys})
        )
        self._watchdog = watchdog if watchdog is not None else Watchdog(self)

    @property
    def state(self) -> SafetyState:
        return self._state

    @property
    def running(self) -> bool:
        return self._state is SafetyState.RUNNING

    @property
    def paused(self) -> bool:
        return self._state is SafetyState.PAUSED

    @property
    def stopped(self) -> bool:
        return self._state is SafetyState.STOPPED

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def hotkeys(self):
        return self._hotkeys

    def start(self) -> None:
        """Lock input and enter the running state."""
        if self._state is not SafetyState.IDLE:
            raise SafetyError("controller has already been started")

        self._lock.start(blocking=True)
        self._worker_stop.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="safety-actions", daemon=True
        )
        self._worker.start()
        self._overlay.start()
        self._overlay.update("running")
        self._watchdog.start()
        self._set_state(SafetyState.RUNNING)
        logger.info("safety controller running")

    def pause(self) -> None:
        """Release input while keeping the shortcut hook alive."""
        if self._state is not SafetyState.RUNNING:
            return
        self._lock.set_blocking(False)
        self._overlay.update("paused")
        self._set_state(SafetyState.PAUSED, "paused")
        logger.info("safety controller paused")

    def resume(self) -> None:
        """Re-lock input and continue."""
        if self._state is not SafetyState.PAUSED:
            return
        if self._lock.active:
            self._lock.set_blocking(True)
        else:
            self._lock.start(blocking=True)
        self._overlay.update("running")
        self._watchdog.beat()
        self._set_state(SafetyState.RUNNING)
        logger.info("safety controller resumed")

    def stop(self, reason: str = "stop") -> None:
        """Release input permanently and enter the stopped state."""
        if self._state is SafetyState.STOPPED:
            return

        self._worker_stop.set()
        self._set_state(SafetyState.STOPPED, reason)
        try:
            self._lock.stop()
        except InputLockError:
            logger.exception("could not remove the input hooks cleanly")
        self._watchdog.stop()
        self._overlay.update("stopped", reason)

        worker = self._worker
        self._worker = None
        if worker is not None and threading.current_thread() is not worker:
            worker.join(timeout=2.0)
        logger.info("safety controller stopped (%s)", reason)

    def check(self) -> bool:
        """Return False if stopped; block while paused. Call between actions."""
        self._watchdog.beat()
        with self._condition:
            while self._state is SafetyState.PAUSED:
                self._watchdog.beat()
                self._condition.wait(timeout=0.2)
            return self._state is SafetyState.RUNNING

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds`` while staying responsive to stop/pause."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if not self.check():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))

    def close(self) -> None:
        self.stop(reason="closed")
        self._overlay.stop()

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                action = self._actions.get(timeout=0.1)
            except queue.Empty:
                continue
            self._apply(action)

    def _apply(self, action: str) -> None:
        if action == "stop":
            self.stop(reason="hotkey: stop")
        elif action == "pause":
            self.pause()
        elif action == "resume":
            self.resume()

    def _set_state(self, state: SafetyState, reason: str = "") -> None:
        with self._condition:
            self._state = state
            if reason:
                self._reason = reason
            self._condition.notify_all()
        if self._on_state is not None:
            try:
                self._on_state(state, reason)
            except Exception:
                logger.exception("state callback failed")

    def __enter__(self) -> "SafetyController":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
