"""Working-copy selection shared by calibration and the batch runner (Segment 7).

The input workbook is never modified. The first command that needs a working
copy copies the input once and records the copy in ``data/state/output.json``.
Later commands reuse that same copy, so a row marked ``DONE`` by calibration is
not processed again by the batch runner.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import AgentConfig, project_root, resolve_path
from ..excel.writer import prepare_output

WORKSPACE_VERSION = 1
DEFAULT_WORKSPACE_STATE = project_root() / "data" / "state" / "output.json"


class WorkspaceError(RuntimeError):
    """The working copy cannot be selected or recorded."""


@dataclass(frozen=True)
class Workspace:
    """The workbook copy that receives status writes."""

    path: Path
    created: bool


@dataclass(frozen=True)
class WorkspaceState:
    """The persisted pointer to the active working copy."""

    input_path: str
    output_path: str
    updated_at: str = ""


def _state_file(path: str | Path | None) -> Path:
    return Path(path) if path is not None else DEFAULT_WORKSPACE_STATE


def load_workspace_state(
    path: str | Path | None = None,
) -> WorkspaceState | None:
    """Read the recorded working copy, or ``None`` when there is none."""
    state_file = _state_file(path)
    if not state_file.is_file():
        return None

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read {state_file}: {exc}") from exc

    if not isinstance(data, dict):
        raise WorkspaceError(f"{state_file} must contain a JSON object")

    input_path = str(data.get("input_path", "")).strip()
    output_path = str(data.get("output_path", "")).strip()
    if not input_path or not output_path:
        raise WorkspaceError(
            f"{state_file} is missing input_path or output_path"
        )
    return WorkspaceState(
        input_path=input_path,
        output_path=output_path,
        updated_at=str(data.get("updated_at", "")),
    )


def save_workspace_state(
    state: WorkspaceState, path: str | Path | None = None
) -> Path:
    """Write the working-copy pointer atomically and return its path."""
    state_file = _state_file(path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": WORKSPACE_VERSION,
        "input_path": state.input_path,
        "output_path": state.output_path,
        "updated_at": state.updated_at
        or datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    temp_name: str | None = None
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=state_file.parent, prefix=".output_", suffix=".json"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.replace(temp_name, state_file)
    except OSError as exc:
        raise WorkspaceError(f"cannot write {state_file}: {exc}") from exc
    finally:
        if temp_name is not None:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
    return state_file


def open_workspace(
    config: AgentConfig, state_path: str | Path | None = None
) -> Workspace:
    """Reuse the recorded working copy, or create a fresh one from the input.

    A recorded copy is reused only when it still exists and belongs to the same
    input workbook. Otherwise a new copy is created and recorded.
    """
    input_path = resolve_path(config.excel.input_path).resolve()
    state = load_workspace_state(state_path)

    if state is not None and state.input_path == str(input_path):
        output = Path(state.output_path)
        if output.is_file() and output.resolve() != input_path:
            return Workspace(path=output, created=False)

    output = prepare_output(config.excel)
    save_workspace_state(
        WorkspaceState(input_path=str(input_path), output_path=str(output)),
        state_path,
    )
    return Workspace(path=output, created=True)


__all__ = [
    "DEFAULT_WORKSPACE_STATE",
    "WORKSPACE_VERSION",
    "Workspace",
    "WorkspaceError",
    "WorkspaceState",
    "load_workspace_state",
    "open_workspace",
    "save_workspace_state",
]
