"""Run lifecycle and resume preflight (Segment 8).

``RunSession`` owns the run-state file. It is written when a run starts, after
every finished row, and again when the run stops, so a closed process can be
resumed from an exact record.

``resume_preflight`` takes a fresh screenshot, re-reads UIA, and resolves the
trusted mapping against the live window before any row is touched. A moved
window is fine; a structural layout change stops the resume with an instruction
to run ``setup`` again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ..ai.mapping import ApprovedMapping
from ..config import AgentConfig, project_root, resolve_path
from ..desktop.capture import capture_window
from ..desktop.uia import inspect_window
from ..desktop.window import attach_window
from ..excel.reader import ExcelRow
from ..models.screen import WindowInfo
from .calibration import ResolvedLayout, resolve_layout
from .row_runner import (
    RowOutcome,
    RunResult,
    layout_fingerprint,
)
from .state_store import (
    DEFAULT_RUN_STATE,
    RunRow,
    RunState,
    load_run_state,
    save_run_state,
)
from .workspace import Workspace


class OrchestratorError(RuntimeError):
    """The run cannot start, resume, or record progress safely."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _absolute(value: str | Path) -> str:
    return str(resolve_path(value).resolve())


def fingerprint_text(fingerprint: tuple[object, ...]) -> str:
    """Serialize a layout fingerprint for the run state."""
    return json.dumps(fingerprint, separators=(",", ":"))


class RunSession:
    """Mutable run progress backed by an atomic JSON file."""

    def __init__(self, state: RunState, path: str | Path) -> None:
        self._state = state
        self.path = Path(path)

    @property
    def state(self) -> RunState:
        return self._state

    def save(self) -> None:
        self._state = replace(self._state, updated_at=_now())
        save_run_state(self._state, self.path)

    def record_layout(self, fingerprint: str) -> None:
        self._state = replace(self._state, layout_fingerprint=fingerprint)

    def observe_layout(
        self, layout: ResolvedLayout, window: WindowInfo
    ) -> None:
        self.record_layout(
            fingerprint_text(layout_fingerprint(layout, window.rect))
        )

    def record(self, outcome: RowOutcome) -> None:
        """Record one finished row and persist immediately."""
        row = RunRow(
            row_number=outcome.row_number,
            status=outcome.status,
            attempts=outcome.attempts,
            error=outcome.error,
        )
        completed = tuple(
            item
            for item in self._state.completed
            if item.row_number != outcome.row_number
        ) + (row,)
        self._state = replace(
            self._state,
            completed=completed,
            updated_at=_now(),
        )
        self.save()

    def finish(self, result: RunResult) -> None:
        """Record the final stop/complete state of a batch."""
        self._state = replace(
            self._state,
            stopped=result.stopped,
            stopped_row=result.stopped_row,
            stop_reason=result.stop_reason,
            updated_at=_now(),
        )
        self.save()

    def abort(self, reason: str) -> None:
        """Mark the run stopped before the normal batch loop returned."""
        remaining = self._state.remaining
        self._state = replace(
            self._state,
            stopped=True,
            stopped_row=remaining[0] if remaining else None,
            stop_reason=reason,
            updated_at=_now(),
        )
        self.save()


def start_run(
    *,
    config_path: str,
    mapping_path: str,
    config: AgentConfig,
    mapping: ApprovedMapping,
    workspace: Workspace,
    window: WindowInfo,
    rows: Sequence[ExcelRow],
    path: str | Path | None = None,
) -> RunSession:
    """Create a new run-state file for the selected rows."""
    state_file = Path(path) if path is not None else DEFAULT_RUN_STATE
    state = RunState(
        run_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
        config_path=_absolute(config_path),
        mapping_path=_absolute(mapping_path),
        mapping_approved_at=mapping.approved_at,
        input_path=_absolute(config.excel.input_path),
        output_path=str(Path(workspace.path).resolve()),
        window_title=window.title,
        window_process=window.process_name,
        window_handle=int(window.handle),
        pending=tuple(row.row_number for row in rows),
        updated_at=_now(),
    )
    save_run_state(state, state_file)
    return RunSession(state, state_file)


def resume_run(
    *,
    state_path: str | Path | None,
    config_path: str,
    mapping_path: str,
    config: AgentConfig,
    mapping: ApprovedMapping,
    workspace: Workspace,
    rows: Sequence[ExcelRow],
) -> RunSession:
    """Load and validate a stopped run, ready to continue."""
    state_file = Path(state_path) if state_path is not None else DEFAULT_RUN_STATE
    state = load_run_state(state_file)
    if state is None:
        raise OrchestratorError(
            f"no run state found at {state_file}; start with "
            "`python main.py run`"
        )
    validate_resume(
        state,
        config_path=config_path,
        mapping_path=mapping_path,
        config=config,
        mapping=mapping,
        workspace=workspace,
        rows=rows,
    )
    return RunSession(state, state_file)


def validate_resume(
    state: RunState,
    *,
    config_path: str,
    mapping_path: str,
    config: AgentConfig,
    mapping: ApprovedMapping,
    workspace: Workspace,
    rows: Sequence[ExcelRow],
) -> None:
    """Refuse to resume when the run no longer describes this workbook."""
    if not mapping.trusted:
        raise OrchestratorError(
            "the approved mapping is not trusted; run "
            "`python main.py calibrate` first"
        )

    checks = (
        ("config", state.config_path, _absolute(config_path)),
        ("mapping", state.mapping_path, _absolute(mapping_path)),
        (
            "input workbook",
            state.input_path,
            _absolute(config.excel.input_path),
        ),
        (
            "working copy",
            state.output_path,
            str(Path(workspace.path).resolve()),
        ),
    )
    for label, recorded, current in checks:
        if recorded != current:
            raise OrchestratorError(
                f"this run state belongs to a different {label}: "
                f"recorded {recorded!r}, current {current!r}; start a new "
                "run instead of resuming"
            )

    if (
        state.mapping_approved_at
        and mapping.approved_at
        and state.mapping_approved_at != mapping.approved_at
    ):
        raise OrchestratorError(
            "the mapping was re-approved after this run stopped; start a "
            "new run so the first row is calibrated again"
        )

    by_number = {row.row_number: row for row in rows}
    missing = [number for number in state.pending if number not in by_number]
    if missing:
        raise OrchestratorError(
            "the workbook no longer contains row(s) "
            + ", ".join(str(number) for number in missing)
            + "; the working copy changed, so this run cannot be resumed"
        )

    for record in state.completed:
        if record.row_number not in state.pending:
            raise OrchestratorError(
                f"the run state records row {record.row_number} as finished "
                "but it is not part of this run"
            )
        row = by_number.get(record.row_number)
        if row is None or not row.status.strip():
            raise OrchestratorError(
                f"the run state records row {record.row_number} as "
                f"{record.status!r} but the workbook has no status for it; "
                "the working copy changed, so this run cannot be resumed"
            )


def resume_rows(
    state: RunState, rows: Sequence[ExcelRow]
) -> tuple[list[ExcelRow], list[ExcelRow]]:
    """Return the rows still to process and the rows already carrying a status.

    A row that already has ``DONE`` or ``RECHECK`` is never processed again.
    This is what guarantees that a stop after Save cannot produce a duplicate
    record when the run resumes.
    """
    by_number = {row.row_number: row for row in rows}
    completed = state.completed_numbers
    selected: list[ExcelRow] = []
    skipped: list[ExcelRow] = []

    for number in state.pending:
        if number in completed:
            continue
        row = by_number.get(number)
        if row is None:
            raise OrchestratorError(
                f"the workbook no longer contains row {number}"
            )
        if row.status.strip():
            skipped.append(row)
            continue
        selected.append(row)

    return selected, skipped


@dataclass(frozen=True)
class ResumePreflight:
    """The fresh look at the target window taken before resuming."""

    window: WindowInfo
    screenshot: Path
    layout: ResolvedLayout
    fingerprint: str
    changed: bool
    warnings: tuple[str, ...] = ()


def resume_preflight(
    session: RunSession,
    *,
    config: AgentConfig,
    mapping: ApprovedMapping,
    window: WindowInfo | None = None,
    screenshot_dir: str | Path | None = None,
) -> ResumePreflight:
    """Screenshot, re-inspect, and re-resolve the map before resuming."""
    live = window if window is not None else attach_window(config.app)
    capture = capture_window(live)

    directory = (
        Path(screenshot_dir)
        if screenshot_dir is not None
        else project_root() / "data" / "screenshots"
    )
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    screenshot = directory / f"resume_{stamp}_preflight.png"
    capture.image.save(screenshot)

    inspection = inspect_window(live)
    layout = resolve_layout(mapping, inspection)
    if layout.problems:
        raise OrchestratorError(
            "the live layout does not match the approved mapping: "
            + "; ".join(layout.problems)
            + "; run `python main.py setup` again"
        )

    fingerprint = fingerprint_text(
        layout_fingerprint(layout, inspection.window.rect)
    )
    changed = bool(session.state.layout_fingerprint) and (
        session.state.layout_fingerprint != fingerprint
    )
    session.record_layout(fingerprint)
    session.save()

    return ResumePreflight(
        window=live,
        screenshot=screenshot,
        layout=layout,
        fingerprint=fingerprint,
        changed=changed,
        warnings=layout.warnings,
    )


__all__ = [
    "OrchestratorError",
    "ResumePreflight",
    "RunSession",
    "fingerprint_text",
    "resume_preflight",
    "resume_rows",
    "resume_run",
    "start_run",
    "validate_resume",
]
