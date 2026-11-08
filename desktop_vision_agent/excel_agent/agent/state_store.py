"""Atomic run-state persistence for stop/resume (Segment 8).

The state file records which rows a run was asked to process, which rows have
finished, and why the run stopped. It is written after every row and again when
the run stops, so a process that is closed mid-run still leaves a usable record.

The workbook status columns remain the authority on whether a row was saved.
The state file only says what the agent intended to do and how far it got.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ..config import project_root

RUN_STATE_VERSION = 1
DEFAULT_RUN_STATE = project_root() / "data" / "state" / "run_state.json"


class StateError(RuntimeError):
    """The run state cannot be read or written."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class RunRow:
    """One finished row as recorded in the run state."""

    row_number: int
    status: str
    attempts: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "row_number": self.row_number,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: object, source: str) -> "RunRow":
        if not isinstance(data, dict):
            raise StateError(f"{source} contains a completed entry that is not an object")
        row_number = data.get("row_number")
        status = data.get("status")
        attempts = data.get("attempts", 0)
        error = data.get("error", "")
        if not isinstance(row_number, int) or isinstance(row_number, bool):
            raise StateError(f"{source} contains a completed entry without a row_number")
        if not isinstance(status, str):
            raise StateError(f"{source} contains a completed entry without a status")
        if not isinstance(attempts, int) or isinstance(attempts, bool):
            raise StateError(f"{source} contains a completed entry with a bad attempts value")
        if not isinstance(error, str):
            raise StateError(f"{source} contains a completed entry with a bad error value")
        return cls(
            row_number=row_number,
            status=status,
            attempts=attempts,
            error=error,
        )


@dataclass(frozen=True)
class RunState:
    """The persisted description of one batch run."""

    version: int = RUN_STATE_VERSION
    run_id: str = ""
    config_path: str = ""
    mapping_path: str = ""
    mapping_approved_at: str = ""
    input_path: str = ""
    output_path: str = ""
    window_title: str = ""
    window_process: str = ""
    window_handle: int = 0
    layout_fingerprint: str = ""
    pending: tuple[int, ...] = ()
    completed: tuple[RunRow, ...] = ()
    stopped: bool = False
    stopped_row: int | None = None
    stop_reason: str = ""
    updated_at: str = ""

    @property
    def completed_numbers(self) -> set[int]:
        return {row.row_number for row in self.completed}

    @property
    def remaining(self) -> tuple[int, ...]:
        """Rows this run still has to process, in their original order."""
        done = self.completed_numbers
        return tuple(number for number in self.pending if number not in done)

    @property
    def unfinished(self) -> bool:
        return self.stopped or bool(self.remaining)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "config_path": self.config_path,
            "mapping_path": self.mapping_path,
            "mapping_approved_at": self.mapping_approved_at,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "window": {
                "title": self.window_title,
                "process": self.window_process,
                "handle": self.window_handle,
            },
            "layout_fingerprint": self.layout_fingerprint,
            "pending": list(self.pending),
            "completed": [row.to_dict() for row in self.completed],
            "stopped": self.stopped,
            "stopped_row": self.stopped_row,
            "stop_reason": self.stop_reason,
            "updated_at": self.updated_at or _now(),
        }

    @classmethod
    def from_dict(cls, data: object, source: str) -> "RunState":
        if not isinstance(data, dict):
            raise StateError(f"{source} must contain a JSON object")

        version = data.get("version")
        if version != RUN_STATE_VERSION:
            raise StateError(
                f"{source} has version {version!r}; expected {RUN_STATE_VERSION}"
            )

        config_path = _required_text(data, "config_path", source)
        mapping_path = _required_text(data, "mapping_path", source)
        input_path = _required_text(data, "input_path", source)
        output_path = _required_text(data, "output_path", source)

        pending = _row_numbers(data.get("pending"), source)
        completed_raw = data.get("completed", [])
        if not isinstance(completed_raw, list):
            raise StateError(f"{source} completed must be a list")
        completed = tuple(
            RunRow.from_dict(item, source) for item in completed_raw
        )

        window = data.get("window", {})
        if not isinstance(window, dict):
            raise StateError(f"{source} window must be an object")
        handle = window.get("handle", 0)
        if not isinstance(handle, int) or isinstance(handle, bool):
            handle = 0

        stopped = data.get("stopped", False)
        if not isinstance(stopped, bool):
            raise StateError(f"{source} stopped must be true or false")
        stopped_row = data.get("stopped_row")
        if stopped_row is not None and (
            not isinstance(stopped_row, int) or isinstance(stopped_row, bool)
        ):
            raise StateError(f"{source} stopped_row must be a row number or null")

        return cls(
            version=RUN_STATE_VERSION,
            run_id=str(data.get("run_id", "")),
            config_path=config_path,
            mapping_path=mapping_path,
            mapping_approved_at=str(data.get("mapping_approved_at", "")),
            input_path=input_path,
            output_path=output_path,
            window_title=str(window.get("title", "")),
            window_process=str(window.get("process", "")),
            window_handle=handle,
            layout_fingerprint=str(data.get("layout_fingerprint", "")),
            pending=pending,
            completed=completed,
            stopped=stopped,
            stopped_row=stopped_row,
            stop_reason=str(data.get("stop_reason", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def _required_text(data: dict, key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{source} is missing {key}")
    return value


def _row_numbers(value: object, source: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise StateError(f"{source} pending must be a list of row numbers")
    numbers: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise StateError(f"{source} pending contains a non-integer row number")
        numbers.append(item)
    return tuple(numbers)


def load_run_state(path: str | Path | None = None) -> RunState | None:
    """Read the run state, or ``None`` when the file does not exist."""
    state_file = Path(path) if path is not None else DEFAULT_RUN_STATE
    if not state_file.is_file():
        return None

    try:
        raw = state_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateError(f"cannot read {state_file}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateError(f"cannot parse {state_file}: {exc}") from exc

    return RunState.from_dict(data, str(state_file))


def save_run_state(
    state: RunState, path: str | Path | None = None
) -> Path:
    """Write the run state atomically and return its path."""
    state_file = Path(path) if path is not None else DEFAULT_RUN_STATE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = replace(state, updated_at=state.updated_at or _now()).to_dict()

    temp_name: str | None = None
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=state_file.parent, prefix=".run_state_", suffix=".json"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.replace(temp_name, state_file)
    except OSError as exc:
        raise StateError(f"cannot write {state_file}: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
    return state_file


__all__ = [
    "DEFAULT_RUN_STATE",
    "RUN_STATE_VERSION",
    "RunRow",
    "RunState",
    "StateError",
    "load_run_state",
    "save_run_state",
]
