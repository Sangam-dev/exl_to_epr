"""Batch row runner over the trusted coordinate map (Segment 7).

Calibration proves the map on the first row. The runner then processes every
pending row with the same deterministic path: resolve the live controls, fill,
read back, Save, and compare the filled fields before and after Save.

Gemini is consulted only when the pixel comparison cannot decide (no comparable
field, a window-size change, or a form that keeps its values). A clean row costs
no AI call.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from ..ai.client import GeminiClient, GeminiError
from ..ai.mapping import ApprovedMapping
from ..ai.verification import check_post_save
from ..config import AgentConfig, project_root
from ..desktop.capture import Capture, CaptureError, capture_window
from ..desktop.input import InputError, click
from ..desktop.uia import UIAError, inspect_window
from ..desktop.window import WindowError, attach_window
from ..excel.reader import ExcelRow, format_value
from ..excel.writer import ExcelWriteError, WorkbookWriter
from ..models.screen import Rect, WindowInfo
from ..models.verification import VerificationError
from ..safety.controller import SafetyController
from .calibration import (
    CalibrationAborted,
    CalibrationError,
    ResolvedLayout,
    SaveEvidence,
    compare_saved_fields,
    fill_row,
    read_back,
    resolve_layout,
)

logger = logging.getLogger(__name__)

SAVED = "saved"
NOT_SAVED = "not_saved"
AMBIGUOUS = "ambiguous"

LayoutObserver = Callable[[ResolvedLayout, WindowInfo], None]


class RunnerError(RuntimeError):
    """The batch cannot continue safely."""


class RunnerAborted(RunnerError):
    """The user stopped the run before the current row was saved."""


@dataclass(frozen=True)
class RowOutcome:
    """The result of one row."""

    row_number: int
    status: str
    attempts: int
    error: str = ""
    screenshots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RunResult:
    """The result of one batch."""

    outcomes: tuple[RowOutcome, ...] = ()
    stopped: bool = False
    stopped_row: int | None = None
    stop_reason: str = ""

    @property
    def processed(self) -> int:
        return len(self.outcomes)

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)


def layout_fingerprint(
    layout: ResolvedLayout, window: Rect
) -> tuple[object, ...]:
    """A stable description of the resolved geometry."""
    return (
        (window.width, window.height),
        tuple(
            (column, target.rect.bbox)
            for column, target in sorted(layout.fields.items())
        ),
        layout.save.rect.bbox if layout.save is not None else None,
    )


def classify_save(evidence: SaveEvidence) -> str:
    """Turn the pixel comparison into a save verdict."""
    if not evidence.compared:
        return AMBIGUOUS
    return NOT_SAVED if evidence.unchanged else SAVED


def _running(controller: SafetyController | None) -> bool:
    return controller is None or controller.check()


def _require_running(
    controller: SafetyController | None, message: str
) -> None:
    if not _running(controller):
        raise RunnerAborted(message)


def _wait(controller: SafetyController | None, seconds: float) -> bool:
    if seconds <= 0:
        return _running(controller)
    if controller is not None:
        return controller.wait(seconds)
    time.sleep(seconds)
    return True


def _click_save(layout: ResolvedLayout) -> None:
    if layout.save is None:
        raise RunnerError("the Save button could not be resolved")
    x, y = layout.save.rect.center
    click(x, y)


def _screenshot(
    capture: Capture,
    directory: Path,
    row_number: int,
    attempt: int,
    phase: str,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = directory / f"run_{stamp}_row{row_number}_attempt{attempt}_{phase}.png"
    directory.mkdir(parents=True, exist_ok=True)
    capture.image.save(path)
    return path


def _heartbeat(
    controller: SafetyController | None,
) -> Callable[[int], None]:
    def beat(_attempt: int) -> None:
        if controller is not None:
            controller.check()

    return beat


def _write_status(
    writer: WorkbookWriter,
    row: ExcelRow,
    status: str,
    error: str = "",
) -> None:
    try:
        writer.write_status(row.row_number, status, error=error)
    except ExcelWriteError as exc:
        raise RunnerError(
            f"could not write {status!r} for row {row.row_number}: {exc}"
        ) from exc


def _finish(
    writer: WorkbookWriter,
    row: ExcelRow,
    status: str,
    *,
    error: str,
    attempts: int,
    screenshots: Sequence[Path],
    output: Callable[[str], None],
) -> RowOutcome:
    _write_status(writer, row, status, error)
    message = f"Row {row.row_number}: {status}"
    if error:
        message += f" - {error}"
    output(message)
    return RowOutcome(
        row_number=row.row_number,
        status=status,
        attempts=attempts,
        error=error,
        screenshots=tuple(screenshots),
    )


def run_row(
    row: ExcelRow,
    *,
    config: AgentConfig,
    mapping: ApprovedMapping,
    writer: WorkbookWriter,
    client: GeminiClient,
    controller: SafetyController | None = None,
    screenshot_dir: str | Path | None = None,
    on_layout: LayoutObserver | None = None,
    output: Callable[[str], None] = print,
) -> RowOutcome:
    """Fill, save, and verify one row."""
    if not mapping.entries:
        raise RunnerError("the approved mapping has no column -> field entries")
    if mapping.button_for_role("save") is None:
        raise RunnerError("the approved mapping has no Save button")

    try:
        values = {
            entry.column: format_value(
                row.values.get(entry.column), entry.type
            )
            for entry in mapping.entries
        }
    except ValueError as exc:
        return _finish(
            writer,
            row,
            config.excel.recheck_value,
            error=f"row {row.row_number} has an invalid value: {exc}",
            attempts=0,
            screenshots=(),
            output=output,
        )

    directory = (
        Path(screenshot_dir)
        if screenshot_dir is not None
        else project_root() / "data" / "screenshots"
    )
    max_attempts = config.safety.max_attempts
    issues: list[str] = []
    screenshots: list[Path] = []
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        _require_running(controller, "stopped before the row was saved")
        output(
            f"Row {row.row_number}: attempt {attempt}/{max_attempts} "
            f"({len(values)} field(s))"
        )
        saved = False
        pre: Capture | None = None
        post: Capture | None = None

        def persist(capture: Capture | None, phase: str) -> None:
            if capture is not None:
                screenshots.append(
                    _screenshot(
                        capture, directory, row.row_number, attempt, phase
                    )
                )

        try:
            window = attach_window(config.app)
            inspection = inspect_window(window)
            layout = resolve_layout(mapping, inspection)
            for warning in layout.warnings:
                output(f"  note: {warning}")
            if layout.problems:
                raise RunnerError(
                    "the live layout does not match the approved mapping: "
                    + "; ".join(layout.problems)
                    + "; run `python main.py setup` again"
                )
            if on_layout is not None:
                on_layout(layout, inspection.window)

            fill_row(
                layout,
                values,
                controller=controller,
                delay=config.safety.action_delay_seconds,
            )
            if not _wait(controller, 0.2):
                raise RunnerAborted(
                    "stopped while filling the form; the row was not saved"
                )

            problems, warnings = read_back(inspection, layout, values)
            for warning in warnings:
                output(f"  note: {warning}")
            if problems:
                message = "; ".join(problems)
                issues.append(f"attempt {attempt}: UIA read-back: {message}")
                output(f"  UIA read-back failed: {message}")
                _wait(controller, 0.5)
                continue

            pre = capture_window(window)
            _require_running(
                controller, "stopped before Save; the row was not saved"
            )
            _click_save(layout)
            saved = True

            if not _wait(controller, config.actions.wait_after_save_seconds):
                persist(pre, "pre_save")
                return _finish(
                    writer,
                    row,
                    config.excel.recheck_value,
                    error="stopped after Save; the record may already exist",
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            post = capture_window(window)
            evidence = compare_saved_fields(pre, post, layout, values)
            kind = classify_save(evidence)
            output(f"  save check: {kind} ({evidence.detail})")

            if kind == SAVED:
                return _finish(
                    writer,
                    row,
                    config.excel.done_value,
                    error="",
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            persist(pre, "pre_save")
            persist(post, "post_save")

            if kind == NOT_SAVED:
                message = "the filled fields are unchanged after Save"
                issues.append(f"attempt {attempt}: {message}")
                output(f"  save not detected: {message}")
                _wait(controller, 0.5)
                continue

            verdict = check_post_save(
                client,
                pre,
                post,
                mapping.entries,
                values,
                config.gemini,
                on_attempt=_heartbeat(controller),
            )
            if not _running(controller):
                return _finish(
                    writer,
                    row,
                    config.excel.recheck_value,
                    error=(
                        "stopped during post-save verification; the record "
                        "may already exist"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            if verdict.approved and not evidence.unchanged:
                return _finish(
                    writer,
                    row,
                    config.excel.done_value,
                    error="",
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            message = "; ".join(verdict.issues) or (
                "the save evidence is ambiguous"
            )
            return _finish(
                writer,
                row,
                config.excel.recheck_value,
                error=(
                    "post-save check uncertain; the record may already exist: "
                    f"{message}"
                ),
                attempts=attempt,
                screenshots=screenshots,
                output=output,
            )

        except CalibrationAborted as exc:
            raise RunnerAborted(str(exc)) from exc
        except WindowError as exc:
            raise RunnerError(f"target window problem: {exc}") from exc
        except RunnerError:
            raise
        except (
            GeminiError,
            VerificationError,
            UIAError,
            CaptureError,
            InputError,
            CalibrationError,
        ) as exc:
            if saved:
                persist(pre, "pre_save")
                persist(post, "post_save")
                return _finish(
                    writer,
                    row,
                    config.excel.recheck_value,
                    error=(
                        "post-save verification failed after Save; the record "
                        f"may already exist: {exc}"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )
            issues.append(f"attempt {attempt}: {exc}")
            output(f"  attempt failed: {exc}")
            _wait(controller, 0.5)

    error = (
        f"row failed after {attempts} attempt(s): "
        + " | ".join(issues[-4:])
    )
    return _finish(
        writer,
        row,
        config.excel.recheck_value,
        error=error,
        attempts=attempts,
        screenshots=screenshots,
        output=output,
    )


def run_rows(
    rows: Sequence[ExcelRow],
    *,
    config: AgentConfig,
    mapping: ApprovedMapping,
    writer: WorkbookWriter,
    client: GeminiClient,
    controller: SafetyController | None = None,
    screenshot_dir: str | Path | None = None,
    limit: int | None = None,
    output: Callable[[str], None] = print,
) -> RunResult:
    """Process pending rows with the trusted map."""
    if not mapping.trusted:
        raise RunnerError(
            "the approved mapping is not trusted; run "
            "`python main.py calibrate` first"
        )
    if limit is not None and limit < 1:
        raise RunnerError(f"limit must be at least 1, got {limit}")

    selected = list(rows[:limit]) if limit is not None else list(rows)
    if not selected:
        return RunResult()

    missing = [
        entry.column
        for entry in mapping.entries
        if entry.column not in selected[0].values
    ]
    if missing:
        raise RunnerError(
            "the mapping references column(s) missing from the workbook: "
            + ", ".join(repr(name) for name in missing)
        )

    baseline: tuple[object, ...] | None = None

    def observe(layout: ResolvedLayout, window: WindowInfo) -> None:
        nonlocal baseline
        fingerprint = layout_fingerprint(layout, window.rect)
        if baseline is None:
            baseline = fingerprint
        elif fingerprint != baseline:
            output(
                "  note: the live layout changed; controls were re-resolved "
                "from UIA"
            )
            baseline = fingerprint

    outcomes: list[RowOutcome] = []
    stopped = False
    stopped_row: int | None = None
    stop_reason = ""

    for row in selected:
        try:
            outcome = run_row(
                row,
                config=config,
                mapping=mapping,
                writer=writer,
                client=client,
                controller=controller,
                screenshot_dir=screenshot_dir,
                on_layout=observe,
                output=output,
            )
        except RunnerAborted as exc:
            stopped = True
            stopped_row = row.row_number
            stop_reason = str(exc)
            output(f"Stopped: {exc}")
            break

        outcomes.append(outcome)
        if (
            outcome.status != config.excel.done_value
            and config.safety.stop_on_failure
        ):
            output("Stopping because safety.stop_on_failure is enabled.")
            break

    return RunResult(
        outcomes=tuple(outcomes),
        stopped=stopped,
        stopped_row=stopped_row,
        stop_reason=stop_reason,
    )


__all__ = [
    "AMBIGUOUS",
    "NOT_SAVED",
    "SAVED",
    "RowOutcome",
    "RunResult",
    "RunnerAborted",
    "RunnerError",
    "classify_save",
    "layout_fingerprint",
    "run_row",
    "run_rows",
]
