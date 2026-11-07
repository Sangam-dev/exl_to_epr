"""Watchdog that releases input hooks when the agent stops making progress."""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class Watchdog:
    """Stop the controller if it stops heartbeating while running.

    The automation loop calls :meth:`beat` (directly or through
    ``SafetyController.check``) between actions. If a run hangs, the watchdog
    releases the input lock so the user is never trapped.
    """

    def __init__(
        self,
        controller,
        timeout_seconds: float = 120.0,
        interval_seconds: float = 1.0,
    ) -> None:
        self._controller = controller
        self._timeout = timeout_seconds
        self._interval = interval_seconds
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.beat()
        self._stop.clear()
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="safety-watchdog", daemon=True
        )
        self._thread.start()

    def beat(self) -> None:
        self._last_beat = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not getattr(self._controller, "running", False):
                continue
            if time.monotonic() - self._last_beat > self._timeout:
                logger.error("watchdog timeout; releasing the input lock")
                self._controller.stop(reason="watchdog: no heartbeat")
                return
