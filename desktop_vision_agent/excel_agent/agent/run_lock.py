"""One run at a time, enforced with a lock file next to the run state.

Two runs at once interleave their clicks and keystrokes into the same
application and race on the same workbook: duplicate records, and statuses
written over each other. The lock is a file that holds the owner's process id;
a lock whose process is gone was left behind by a crash and is taken over.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..config import project_root

DEFAULT_RUN_LOCK = project_root() / "data" / "state" / "run.lock"

# Windows: a process that is still running reports this exit code.
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class RunLockError(RuntimeError):
    """Another run is already using this application and workbook."""


def _pid_alive(pid: int) -> bool:
    """True when *pid* is a running process; False when it is gone."""
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            # Access denied means the process exists but is not ours to query.
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return int(code.value) == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # pragma: no cover - non-Windows
        return False


class RunLock:
    """Hold ``run.lock`` for the length of a run."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_RUN_LOCK
        self._held = False

    def acquire(self, note: str = "") -> None:
        """Take the lock, or raise when a live run already holds it."""
        if self._held:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "started_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "note": note,
            }
        )

        for _attempt in (1, 2):
            try:
                handle = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                owner = self._owner()
                if owner is not None and _pid_alive(owner):
                    raise RunLockError(
                        f"another run is already active (pid {owner}) - finish "
                        f"it or press Alt+T there first; if it has crashed, "
                        f"delete {self.path}"
                    ) from None
                # The owner is gone: a crash left this behind.
                self._remove()
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(payload)
            self._held = True
            return

        raise RunLockError(f"could not take the run lock at {self.path}")

    def release(self) -> None:
        """Drop the lock if this process holds it."""
        if not self._held:
            return
        self._held = False
        if self._owner() == os.getpid():
            self._remove()

    def _owner(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pid = data.get("pid") if isinstance(data, dict) else None
        return int(pid) if isinstance(pid, int) else None

    def _remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RunLockError(f"cannot remove {self.path}: {exc}") from exc

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = ["DEFAULT_RUN_LOCK", "RunLock", "RunLockError"]
