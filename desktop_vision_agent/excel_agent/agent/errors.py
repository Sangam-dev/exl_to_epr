"""Exceptions shared by the row runners.

They live here rather than in :mod:`excel_agent.agent.row_runner` so the pause
guard can raise ``RunnerAborted`` without importing the runner that uses it.
``row_runner`` re-exports both names, so existing imports keep working.
"""

from __future__ import annotations


class RunnerError(RuntimeError):
    """The batch cannot continue safely."""


class RunnerAborted(RunnerError):
    """The user stopped the run before the current row was saved."""


__all__ = ["RunnerAborted", "RunnerError"]
