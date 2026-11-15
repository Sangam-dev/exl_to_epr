"""Pause/resume handling shared by both row runners.

A pause hands the mouse and keyboard back to the user, who may move the window,
cover it, close the form, or leave another control focused. Picking up where it
left off with the old screen snapshot would click and type into whatever is in
front, so the first check after a pause brings the target window back to the
front, and the caller is told to read the screen again before aiming.
"""

from __future__ import annotations

import time
from typing import Callable

from ..desktop.window import focus_window
from ..models.screen import WindowInfo
from ..safety.controller import SafetyController
from .errors import RunnerAborted


class PauseGuard:
    """Honour pause/resume between actions and re-aim after a resume."""

    def __init__(
        self,
        controller: SafetyController | None,
        window: WindowInfo | None = None,
        output: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._controller = controller
        self._window = window
        self._output = output
        self._sleep = sleep
        self._pauses = getattr(controller, "pause_count", 0)

    def check(self, message: str) -> bool:
        """Block while paused and raise when stopped.

        Returns True when the run was paused and has just resumed, so the caller
        knows its screen snapshot can no longer be trusted.
        """
        if self._controller is None:
            return False
        if not self._controller.check():
            raise RunnerAborted(message)
        pauses = getattr(self._controller, "pause_count", self._pauses)
        if pauses == self._pauses:
            return False
        self._pauses = pauses
        if not self._refocus():
            # Continuing would click and type into whatever is in front now.
            raise RunnerAborted(
                "the window could not be brought back to the front after the "
                "pause, so the run stopped instead of clicking blind"
            )
        return True

    def _refocus(self) -> bool:
        """Bring the window back; False means the run must not continue."""
        if self._window is None:
            return True
        for attempt in (1, 2):
            try:
                if focus_window(self._window):
                    self._output(
                        "  resumed: the window was brought back to the front"
                    )
                    return True
            except Exception as exc:  # a dead handle must not crash the run
                self._output(
                    f"  resumed: could not bring the window forward ({exc})"
                )
                return False
            if attempt == 1:
                self._sleep(0.2)
        return False


__all__ = ["PauseGuard"]
