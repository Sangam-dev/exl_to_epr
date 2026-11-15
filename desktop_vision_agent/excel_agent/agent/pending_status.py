"""Pending workbook-status sidecar (Segment 10).

A Save can succeed while the workbook cannot be written (for example, the file
is open in Excel). The outcome is persisted here, next to ``run_state.json``,
and replayed before any row is processed on the next run or resume.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import project_root

PENDING_STATUS_VERSION = 1
DEFAULT_PENDING_STATUS = project_root() / "data" / "state" / "pending_status.json"

StatusWriter = Callable[..., None]


class PendingStatusError(RuntimeError):
    """The pending-status sidecar cannot be read or written."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class PendingStatus:
    """One outcome that still has to reach the workbook."""

    row_number: int
    status: str
    error: str = ""
    attempts: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "row_number": self.row_number,
            "status": self.status,
            "error": self.error,
            "attempts": self.attempts,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: object, source: str) -> "PendingStatus":
        if not isinstance(data, dict):
            raise PendingStatusError(
                f"{source} contains a pending entry that is not an object"
            )
        row_number = data.get("row_number")
        status = data.get("status")
        error = data.get("error", "")
        attempts = data.get("attempts", 0)
        created_at = data.get("created_at", "")
        if not isinstance(row_number, int) or isinstance(row_number, bool):
            raise PendingStatusError(
                f"{source} contains a pending entry without a row_number"
            )
        if not isinstance(status, str) or not status.strip():
            raise PendingStatusError(
                f"{source} contains a pending entry without a status"
            )
        if not isinstance(error, str):
            raise PendingStatusError(
                f"{source} contains a pending entry with a bad error value"
            )
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            raise PendingStatusError(
                f"{source} contains a pending entry with a bad attempts value"
            )
        if not isinstance(created_at, str):
            raise PendingStatusError(
                f"{source} contains a pending entry with a bad created_at value"
            )
        return cls(
            row_number=row_number,
            status=status,
            error=error,
            attempts=attempts,
            created_at=created_at,
        )


@dataclass(frozen=True)
class PendingStatusFile:
    """The whole sidecar document."""

    version: int = PENDING_STATUS_VERSION
    entries: tuple[PendingStatus, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(
        cls, data: object, source: str
    ) -> "PendingStatusFile":
        if not isinstance(data, dict):
            raise PendingStatusError(f"{source} must contain a JSON object")
        version = data.get("version")
        if version != PENDING_STATUS_VERSION:
            raise PendingStatusError(
                f"{source} has version {version!r}; expected "
                f"{PENDING_STATUS_VERSION}"
            )
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            raise PendingStatusError(f"{source} entries must be a list")
        return cls(
            version=PENDING_STATUS_VERSION,
            entries=tuple(
                PendingStatus.from_dict(item, source)
                for item in raw_entries
            ),
        )


def load_pending_status(
    path: str | Path | None = None,
) -> PendingStatusFile | None:
    """Read the sidecar, or ``None`` when it does not exist."""
    sidecar = Path(path) if path is not None else DEFAULT_PENDING_STATUS
    if not sidecar.is_file():
        return None
    try:
        raw = sidecar.read_text(encoding="utf-8")
    except OSError as exc:
        raise PendingStatusError(f"cannot read {sidecar}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PendingStatusError(f"cannot parse {sidecar}: {exc}") from exc
    return PendingStatusFile.from_dict(data, str(sidecar))


def save_pending_status(
    file: PendingStatusFile, path: str | Path | None = None
) -> Path:
    """Write the sidecar atomically and return its path."""
    sidecar = Path(path) if path is not None else DEFAULT_PENDING_STATUS
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    payload = file.to_dict()

    temp_name: str | None = None
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=sidecar.parent, prefix=".pending_status_", suffix=".json"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.replace(temp_name, sidecar)
    except OSError as exc:
        raise PendingStatusError(f"cannot write {sidecar}: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
    return sidecar


def queue_pending_status(
    *,
    row_number: int,
    status: str,
    error: str = "",
    path: str | Path | None = None,
    attempts: int = 0,
) -> PendingStatusFile:
    """Add or replace one row's pending outcome and persist it."""
    sidecar = Path(path) if path is not None else DEFAULT_PENDING_STATUS
    current = load_pending_status(sidecar)
    entries = list(current.entries) if current is not None else []
    entries = [entry for entry in entries if entry.row_number != row_number]
    entries.append(
        PendingStatus(
            row_number=row_number,
            status=status,
            error=error,
            attempts=attempts,
            created_at=_now(),
        )
    )
    queued = PendingStatusFile(entries=tuple(entries))
    save_pending_status(queued, sidecar)
    return queued


def write_status_with_retries(
    write_status: StatusWriter,
    row_number: int,
    status: str,
    error: str = "",
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, str]:
    """Try to write one status; return ``(ok, last_failure)``."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_failure = ""
    for index in range(attempts):
        try:
            write_status(row_number, status, error=error)
            return True, ""
        except Exception as exc:
            last_failure = str(exc) or type(exc).__name__
            if index + 1 < attempts:
                sleep(base_delay * (2 ** index))
    return False, last_failure


def _delete(sidecar: Path) -> None:
    try:
        sidecar.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise PendingStatusError(f"cannot remove {sidecar}: {exc}") from exc


def apply_pending_statuses(
    write_status: StatusWriter,
    path: str | Path | None = None,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> tuple[tuple[int, ...], tuple[PendingStatus, ...]]:
    """Replay every queued status; keep the ones that still fail."""
    sidecar = Path(path) if path is not None else DEFAULT_PENDING_STATUS
    queued = load_pending_status(sidecar)
    if queued is None or not queued.entries:
        return (), ()

    applied: list[int] = []
    remaining: list[PendingStatus] = []
    for entry in queued.entries:
        ok, failure = write_status_with_retries(
            write_status,
            entry.row_number,
            entry.status,
            entry.error,
            attempts=attempts,
            base_delay=base_delay,
            sleep=sleep,
        )
        if ok:
            applied.append(entry.row_number)
            output(
                f"Applied pending status {entry.status!r} to row "
                f"{entry.row_number}"
            )
            continue
        detail = f"retry failed: {failure}"
        remaining.append(
            replace(
                entry,
                attempts=entry.attempts + 1,
                error=(
                    f"{entry.error} | {detail}" if entry.error else detail
                ),
            )
        )

    if remaining:
        save_pending_status(
            PendingStatusFile(entries=tuple(remaining)), sidecar
        )
    else:
        _delete(sidecar)
    return tuple(applied), tuple(remaining)


__all__ = [
    "DEFAULT_PENDING_STATUS",
    "PENDING_STATUS_VERSION",
    "PendingStatus",
    "PendingStatusError",
    "PendingStatusFile",
    "apply_pending_statuses",
    "load_pending_status",
    "queue_pending_status",
    "save_pending_status",
    "write_status_with_retries",
]
