"""Static form filler: run a config-declared click/fill plan once per row.

The mapped pipeline (``setup`` -> ``calibrate`` -> ``run``) grounds a form with
AI and proves the map on the first row. This module covers the smaller case: a
form whose controls can be named in the config, where the only thing a mapping
cannot express is *order* -- click "Add" to open the entry fields, type three
text boxes, click Save.

Only two actions exist, ``click`` and ``fill``. Values come from the workbook,
one row per record. Every fill is read back before Save, and a Save is never
clicked twice: only a ``CONFIRMED`` save becomes ``DONE``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from ..config import AgentConfig, FormConfig, project_root
from ..desktop.capture import Capture, CaptureError, capture_window
from ..desktop.input import (
    VK_A,
    VK_CONTROL,
    VK_DELETE,
    InputError,
    click_checked,
    paste_text,
    press_chord,
    press_key,
)
from ..desktop.uia import UIAError, UIAInspection, inspect_window
from ..desktop.window import WindowError, attach_window, focus_window
from ..excel.reader import ExcelRow, format_value
from ..excel.writer import WorkbookWriter
from ..models.actions import ClickStep, FillStep, FormStep
from ..models.screen import WindowInfo
from ..safety.controller import SafetyController
from .calibration import (
    EDITABLE_CONTROL_TYPES,
    ResolvedLayout,
    ResolvedTarget,
    compare_saved_fields,
    resolve_page_target,
    values_match,
)
from .confirmation import (
    CONFIRMED,
    REJECTED,
    UNKNOWN,
    ConfirmationError,
    ConfirmationResult,
    SaveConfirmer,
)
from .row_evidence import check_row_appeared, footprint_columns

logger = logging.getLogger(__name__)
from .row_search import RowSearchUnsupported, find_appended_row
from .pending_status import (
    DEFAULT_PENDING_STATUS,
    PendingStatusError,
    queue_pending_status,
    write_status_with_retries,
)
from .row_runner import RowOutcome, RunResult, RunnerAborted, RunnerError

BUTTON_CONTROL_TYPES = frozenset({"Button"})


@dataclass(frozen=True)
class PlanResult:
    """What one pass over the step plan did."""

    inspection: UIAInspection
    fields: dict[str, ResolvedTarget]
    steps_run: int = 0
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def _running(controller: SafetyController | None) -> bool:
    return controller is None or controller.check()


def _require_running(
    controller: SafetyController | None, message: str
) -> None:
    if not _running(controller):
        raise RunnerAborted(message)


class _PauseGuard:
    """Honour pause/resume between actions and re-aim after a resume.

    A pause hands the mouse and keyboard back to the user, who may move the
    window, cover it, close the form, or leave another control focused. Picking
    up where it left off with the old screen snapshot would click and type into
    whatever is in front, so the first check after a pause brings the target
    window back to the front and reports that the run was aimed again.
    """

    def __init__(
        self,
        controller: SafetyController | None,
        window: WindowInfo | None = None,
        output: Callable[[str], None] = print,
    ) -> None:
        self._controller = controller
        self._window = window
        self._output = output
        self._pauses = getattr(controller, "pause_count", 0)

    def check(self, message: str) -> bool:
        """Block while paused and raise when stopped.

        Returns True when the run was paused and has just resumed, so the
        caller knows its screen snapshot can no longer be trusted.
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
                self._output(f"  resumed: could not bring the window forward ({exc})")
                return False
            if attempt == 1:
                time.sleep(0.2)
        return False


def _wait(controller: SafetyController | None, seconds: float) -> bool:
    if seconds <= 0:
        return _running(controller)
    if controller is not None:
        return controller.wait(seconds)
    time.sleep(seconds)
    return True


def _ask_before_save(row: ExcelRow, input_func: Callable[[str], str]) -> None:
    answer = input_func(
        f"Row {row.row_number}: press Enter or Y to click Save "
        "(anything else stops): "
    )
    if answer.strip().casefold() not in ("", "y", "yes"):
        raise RunnerAborted(
            f"Save declined by the user for row {row.row_number}; the row "
            "was not saved"
        )


def add_button_labels(form: FormConfig) -> list[str]:
    """The plan's first click: the button that opens the entry form."""
    for step in form.steps:
        if isinstance(step, ClickStep):
            return [step.button] if step.button else []
    return []


def step_columns(form: FormConfig) -> list[str]:
    """Every column the plan fills, in first-use order."""
    columns: dict[str, None] = {}
    for step in form.steps:
        if isinstance(step, FillStep):
            columns.setdefault(step.column, None)
    return list(columns)


def column_values(
    row: ExcelRow, config: AgentConfig, form: FormConfig
) -> dict[str, str]:
    """Format the plan's columns for one row.

    Raises ``ValueError`` when a value cannot be formatted for its declared
    type, so a bad row is reported instead of being typed into the form.
    """
    types = {item.column: item.type for item in config.fields}
    return {
        column: format_value(row.values.get(column), types[column])
        for column in step_columns(form)
    }


# How long to wait before reading a field again when the first read disagrees.
READ_BACK_RETRY_SECONDS = 0.15


def execute_form_steps(
    steps: Sequence[FormStep],
    values: dict[str, str],
    *,
    window: WindowInfo,
    inspection: UIAInspection,
    labels: dict[str, str],
    types: dict[str, str],
    controller: SafetyController | None = None,
    delay: float = 0.0,
    pause_after_click: float = 0.5,
    require_foreground: bool = False,
    read_back_retry_seconds: float = READ_BACK_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> PlanResult:
    """Run the plan in order, re-reading the live UI after every click.

    A click may create the controls the next steps need, so the tree is
    re-inspected after each one; a fill never changes the tree, so its
    read-back uses the inspection that resolved it.
    """
    fields: dict[str, ResolvedTarget] = {}
    problems: list[str] = []
    warnings: list[str] = []
    executed = 0
    guard = _PauseGuard(controller, window, output)

    for index, step in enumerate(steps, start=1):
        if guard.check("stopped while filling the form"):
            # The user took over while the run was paused, so the screen may
            # have changed underneath it: read the live UI again before aiming.
            try:
                inspection = inspect_window(window)
            except UIAError as exc:
                problems.append(
                    f"step {index}: could not re-read the window after the "
                    f"pause: {exc}"
                )
                break

        if isinstance(step, ClickStep):
            action = "click"
            label = step.button
            control_types = BUTTON_CONTROL_TYPES
        else:
            action = "fill"
            label = step.field or labels.get(step.column, "")
            control_types = EDITABLE_CONTROL_TYPES

        if not label:
            problems.append(
                f"step {index}: column {step.column!r} has no field label; "
                "add \"field\" to the step"
            )
            break

        target, warning = resolve_page_target(
            inspection, label=label, control_types=control_types
        )
        if warning:
            warnings.append(f"step {index}: {warning}")
        if target is None:
            message = (
                f"step {index} ({action} {label!r}): no match in the live "
                "window"
            )
            if step.optional:
                warnings.append(f"{message}; skipped")
                output(f"  {message}; skipped")
                continue
            problems.append(message)
            break

        x, y = target.rect.center
        try:
            click_checked(
                x,
                y,
                window,
                require_foreground=require_foreground,
            )
        except InputError as exc:
            problems.append(f"step {index} ({action} {label!r}): {exc}")
            break
        executed += 1

        if isinstance(step, ClickStep):
            if pause_after_click > 0:
                sleep(pause_after_click)
            output(f"  step {index}: click {label!r}")
            try:
                inspection = inspect_window(window)
            except UIAError as exc:
                problems.append(
                    f"step {index}: could not re-read the window after "
                    f"clicking {label!r}: {exc}"
                )
                break
            continue

        if delay > 0:
            sleep(delay)
        if guard.check("stopped while filling the form"):
            # Pausing between the click and the paste would leave the field
            # selected and send the paste wherever the user left the focus:
            # click the field again before typing into it.
            again_x, again_y = target.rect.center
            try:
                click_checked(
                    again_x,
                    again_y,
                    window,
                    require_foreground=require_foreground,
                )
            except InputError as exc:
                problems.append(f"step {index} ({action} {label!r}): {exc}")
                break
            if delay > 0:
                sleep(delay)
        value = values.get(step.column, "")
        press_chord(VK_CONTROL, VK_A)
        if value:
            paste_text(value)
        else:
            press_key(VK_DELETE)
        if delay > 0:
            sleep(delay)
        fields[step.column] = target
        output(f"  step {index}: fill {label!r} = {value!r}")

        if target.candidate_id is None:
            continue
        try:
            observed = inspection.read_value(target.candidate_id)
        except UIAError as exc:
            warnings.append(
                f"step {index}: could not read {label!r} back ({exc}); "
                "relying on the save check"
            )
            continue
        expected = value
        field_type = types.get(step.column, "text")
        if not values_match(observed, expected, field_type) and (
            read_back_retry_seconds > 0
        ):
            # The target applies a paste a moment after the keystroke, so the
            # first read can arrive before the text does. Look once more before
            # failing a row that was actually filled correctly.
            sleep(read_back_retry_seconds)
            try:
                observed = inspection.read_value(target.candidate_id)
            except UIAError as exc:
                warnings.append(
                    f"step {index}: could not read {label!r} back again "
                    f"({exc}); relying on the save check"
                )
                continue
        if not values_match(observed, expected, field_type):
            problems.append(
                f"step {index} ({label!r}): read back {observed!r}, "
                f"expected {expected!r}"
            )
            break

    return PlanResult(
        inspection=inspection,
        fields=fields,
        steps_run=executed,
        problems=tuple(problems),
        warnings=tuple(warnings),
    )


def _screenshot(
    capture: Capture,
    directory: Path,
    row_number: int,
    attempt: int,
    phase: str,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = directory / f"form_{stamp}_row{row_number}_attempt{attempt}_{phase}.png"
    directory.mkdir(parents=True, exist_ok=True)
    capture.image.save(path)
    return path


def _record(
    writer: WorkbookWriter,
    row: ExcelRow,
    status: str,
    *,
    error: str,
    attempts: int,
    screenshots: Sequence[Path],
    output: Callable[[str], None],
    pending_status_path: str | Path | None = None,
    status_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> RowOutcome:
    """Write the row's status, queueing it when the workbook refuses."""
    ok, failure = write_status_with_retries(
        writer.write_status,
        row.row_number,
        status,
        error,
        attempts=status_attempts,
        sleep=sleep,
    )
    if ok:
        message = f"Row {row.row_number}: {status}"
        if error:
            message += f" - {error}"
        output(message)
        logger.info(
            "row %s: %s (attempts=%s)%s",
            row.row_number,
            status,
            attempts,
            f" - {error}" if error else "",
        )
        return RowOutcome(
            row_number=row.row_number,
            status=status,
            attempts=attempts,
            error=error,
            screenshots=tuple(screenshots),
        )

    sidecar = (
        Path(pending_status_path)
        if pending_status_path is not None
        else DEFAULT_PENDING_STATUS
    )
    note = (
        f"status {status!r} could not be written after {status_attempts} "
        f"attempt(s): {failure}; queued in {sidecar}"
    )
    try:
        queue_pending_status(
            row_number=row.row_number,
            status=status,
            error=error,
            path=sidecar,
        )
    except PendingStatusError as exc:
        note += f"; the pending-status sidecar could not be written: {exc}"
    combined = f"{error} | {note}" if error else note
    output(f"Row {row.row_number}: {status} - {note}")
    return RowOutcome(
        row_number=row.row_number,
        status=status,
        attempts=attempts,
        error=combined,
        screenshots=tuple(screenshots),
        status_written=False,
    )


def _read_save_window(window: WindowInfo) -> UIAInspection | None:
    """The screen a save is checked against, wide enough to see table rows."""
    try:
        return inspect_window(window, include_data_items=True)
    except UIAError:
        return None


def _verify_save(
    *,
    config: AgentConfig,
    confirmation: SaveConfirmer | None,
    window: WindowInfo,
    before: UIAInspection | None,
    post: Capture,
    evidence: str,
    values: dict[str, str],
    anchor_labels: Sequence[str] = (),
) -> ConfirmationResult:
    """Decide whether the Save worked: the model reads the screen, or we do.

    ``verifier: "row"`` is the local path - no model call, no screenshot sent
    anywhere. It needs the screen as it was before the click, so a row that was
    already listed cannot be mistaken for the new one. When the new row landed
    below the fold the screen is scrolled down looking for it, never upwards.
    ``verifier: "none"`` is the "just fill and save it" mode: the screen is not
    read at all, and every Save counts as done.
    """
    if config.confirmation.verifier == "none":
        return ConfirmationResult(
            outcome=CONFIRMED,
            reason="the screen is not checked (verifier: none)",
            source="no check",
        )
    if config.confirmation.verifier == "row":
        try:
            after = inspect_window(window, include_data_items=True)
        except UIAError as exc:
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=f"could not read the window after Save: {exc}",
                source="window text",
            )
        footprint = footprint_columns(config)
        result = check_row_appeared(
            before,
            after,
            values,
            footprint,
            config.confirmation.error_patterns,
        )
        if (
            result.outcome in (CONFIRMED, REJECTED)
            or not config.scrolling.enabled
        ):
            return result
        search = find_appended_row(
            after,
            window=window,
            before=before,
            values=values,
            footprint=footprint,
            error_patterns=config.confirmation.error_patterns,
            anchor_labels=anchor_labels,
            max_screens=config.scrolling.max_screens,
            settle_seconds=config.scrolling.settle_seconds,
            wheel_notches=config.scrolling.wheel_notches,
            note=print,
        )
        if search.fatal:
            raise RowSearchUnsupported(search.fatal)
        return search.result
    if confirmation is None:
        return ConfirmationResult(
            outcome=UNKNOWN,
            reason="no text confirmer was given, so the Save cannot be confirmed",
        )
    return confirmation.confirm(post.image, weak_hint=evidence)


def run_form_row(
    row: ExcelRow,
    *,
    config: AgentConfig,
    writer: WorkbookWriter,
    confirmation: SaveConfirmer | None = None,
    controller: SafetyController | None = None,
    on_before_save: Callable[[ExcelRow], None] | None = None,
    confirm_each: bool = False,
    input_func: Callable[[str], str] | None = None,
    screenshot_dir: str | Path | None = None,
    pending_status_path: str | Path | None = None,
    status_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = print,
) -> RowOutcome:
    """Run the plan and Save one row.

    The plan is retried only when ``form.retry_on_failure`` is set: a click in
    the plan may create a record, so replaying it can duplicate work.
    """
    form = config.form
    if form is None or not form.steps:
        raise RunnerError("this config has no form.steps to run")
    if input_func is None:
        input_func = input
    labels = {item.column: item.field for item in config.fields}
    types = {item.column: item.type for item in config.fields}
    directory = (
        Path(screenshot_dir)
        if screenshot_dir is not None
        else project_root() / "data" / "screenshots"
    )

    def finish(
        status: str,
        *,
        error: str,
        attempts: int,
        screenshots: Sequence[Path],
    ) -> RowOutcome:
        return _record(
            writer,
            row,
            status,
            error=error,
            attempts=attempts,
            screenshots=screenshots,
            output=output,
            pending_status_path=pending_status_path,
            status_attempts=status_attempts,
            sleep=sleep,
        )

    try:
        values = column_values(row, config, form)
    except ValueError as exc:
        return finish(
            config.excel.recheck_value,
            error=f"row {row.row_number} has an invalid value: {exc}",
            attempts=0,
            screenshots=(),
        )

    max_attempts = config.safety.max_attempts if form.retry_on_failure else 1
    issues: list[str] = []
    screenshots: list[Path] = []
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        _require_running(controller, "stopped before the row was saved")
        output(
            f"Row {row.row_number}: attempt {attempt}/{max_attempts} "
            f"({len(form.steps)} step(s))"
        )
        logger.info(
            "row %s: attempt %s/%s, %s step(s)",
            row.row_number,
            attempt,
            max_attempts,
            len(form.steps),
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
            guard = _PauseGuard(controller, window, output)
            plan = execute_form_steps(
                form.steps,
                values,
                window=window,
                inspection=inspection,
                labels=labels,
                types=types,
                controller=controller,
                delay=config.safety.action_delay_seconds,
                pause_after_click=form.pause_after_click_seconds,
                require_foreground=config.app.require_foreground,
                sleep=sleep,
                output=output,
            )
            for warning in plan.warnings:
                output(f"  note: {warning}")
            if plan.problems:
                message = "; ".join(plan.problems)
                issues.append(f"attempt {attempt}: {message}")
                output(f"  steps failed: {message}")
                if form.retry_on_failure:
                    _wait(controller, 0.5)
                    continue
                return finish(
                    config.excel.recheck_value,
                    error=(
                        f"step plan failed before Save: {message}; the row "
                        "was not saved"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                )

            if not _wait(controller, 0.2):
                raise RunnerAborted(
                    "stopped while filling the form; the row was not saved"
                )

            save, warning = resolve_page_target(
                plan.inspection,
                label=config.actions.save_button,
                control_types=BUTTON_CONTROL_TYPES,
            )
            if warning:
                output(f"  note: Save: {warning}")
            if save is None:
                message = (
                    f"the Save button {config.actions.save_button!r} was not "
                    "found in the live window"
                )
                issues.append(f"attempt {attempt}: {message}")
                output(f"  {message}")
                if form.retry_on_failure:
                    _wait(controller, 0.5)
                    continue
                return finish(
                    config.excel.recheck_value,
                    error=f"{message}; the row was not saved",
                    attempts=attempt,
                    screenshots=screenshots,
                )

            layout = ResolvedLayout(
                fields=dict(plan.fields),
                save=save,
                warnings=plan.warnings,
            )
            before_evidence = (
                _read_save_window(window)
                if config.confirmation.verifier == "row"
                else None
            )

            pre = capture_window(window)
            if guard.check("stopped before Save; the row was not saved"):
                # The form may have been closed or moved while the run was
                # paused: find the Save button again instead of clicking a
                # remembered point on the screen.
                try:
                    after_pause = inspect_window(window)
                except UIAError as exc:
                    return finish(
                        config.excel.recheck_value,
                        error=(
                            "stopped while paused and the window could not be "
                            f"read again ({exc}); the row was not saved"
                        ),
                        attempts=attempt,
                        screenshots=screenshots,
                    )
                save, warning = resolve_page_target(
                    after_pause,
                    label=config.actions.save_button,
                    control_types=BUTTON_CONTROL_TYPES,
                )
                if warning:
                    output(f"  note: Save: {warning}")
                if save is None:
                    message = (
                        "the form was closed or changed while the run was "
                        f"paused, and the Save button "
                        f"{config.actions.save_button!r} is no longer there"
                    )
                    issues.append(f"attempt {attempt}: {message}")
                    output(f"  {message}")
                    return finish(
                        config.excel.recheck_value,
                        error=f"{message}; the row was not saved",
                        attempts=attempt,
                        screenshots=screenshots,
                    )
            if confirm_each:
                _ask_before_save(row, input_func)
            if on_before_save is not None:
                on_before_save(row)

            x, y = save.rect.center
            try:
                click_checked(
                    x,
                    y,
                    window,
                    require_foreground=config.app.require_foreground,
                )
            except InputError as exc:
                message = f"{exc}; the row was not saved"
                issues.append(f"attempt {attempt}: {message}")
                output(f"  {message}")
                return finish(
                    config.excel.recheck_value,
                    error=message,
                    attempts=attempt,
                    screenshots=screenshots,
                )
            saved = True

            if not _wait(controller, config.actions.wait_after_save_seconds):
                persist(pre, "pre_save")
                return finish(
                    config.excel.recheck_value,
                    error="stopped after Save; the record may already exist",
                    attempts=attempt,
                    screenshots=screenshots,
                )

            post = capture_window(window)
            evidence = compare_saved_fields(pre, post, layout, values)
            output(f"  save hint: {evidence.detail}")
            try:
                result = _verify_save(
                    config=config,
                    confirmation=confirmation,
                    window=window,
                    before=before_evidence,
                    post=post,
                    evidence=evidence.detail,
                    values=values,
                    anchor_labels=add_button_labels(form),
                )
            except RowSearchUnsupported as exc:
                # The record may exist, so record the row before stopping: a
                # status is safer than leaving it blank for a re-run to repeat.
                persist(pre, "pre_save")
                persist(post, "post_save")
                message = f"{exc}; stopping before the next row"
                finish(
                    config.excel.recheck_value,
                    error=message,
                    attempts=attempt,
                    screenshots=screenshots,
                )
                raise RunnerAborted(message) from exc
            output(
                f"  save confirmation: {result.outcome} ({result.reason})"
            )
            logger.info(
                "row %s: save verification %s (%s) [%s]",
                row.row_number,
                result.outcome,
                result.reason,
                result.source,
            )

            if result.outcome == CONFIRMED:
                return finish(
                    config.excel.done_value,
                    error="",
                    attempts=attempt,
                    screenshots=screenshots,
                )

            persist(pre, "pre_save")
            persist(post, "post_save")
            if result.outcome == REJECTED:
                message = (
                    f"save rejected: {result.reason}; the record may already "
                    "exist, so the row was not saved again"
                )
            else:
                message = (
                    "record may already exist: "
                    f"{result.reason or 'the save could not be confirmed'}"
                )
            issues.append(f"attempt {attempt}: {message}")
            output(f"  save not confirmed: {message}")
            return finish(
                config.excel.recheck_value,
                error=message,
                attempts=attempt,
                screenshots=screenshots,
            )

        except RunnerAborted:
            raise
        except WindowError as exc:
            raise RunnerError(f"target window problem: {exc}") from exc
        except RunnerError:
            raise
        except (
            ConfirmationError,
            UIAError,
            CaptureError,
            InputError,
        ) as exc:
            if saved:
                persist(pre, "pre_save")
                persist(post, "post_save")
                return finish(
                    config.excel.recheck_value,
                    error=(
                        "post-save verification failed after Save; the record "
                        f"may already exist: {exc}"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                )
            issues.append(f"attempt {attempt}: {exc}")
            output(f"  attempt failed: {exc}")
            _wait(controller, 0.5)

    error = (
        f"row failed after {attempts} attempt(s): " + " | ".join(issues[-4:])
    )
    return finish(
        config.excel.recheck_value,
        error=error,
        attempts=attempts,
        screenshots=screenshots,
    )


def run_form_rows(
    rows: Sequence[ExcelRow],
    *,
    config: AgentConfig,
    writer: WorkbookWriter,
    confirmation: SaveConfirmer | None = None,
    controller: SafetyController | None = None,
    on_before_save: Callable[[ExcelRow], None] | None = None,
    confirm_each: bool = False,
    input_func: Callable[[str], str] | None = None,
    screenshot_dir: str | Path | None = None,
    pending_status_path: str | Path | None = None,
    status_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    limit: int | None = None,
    on_row: Callable[[RowOutcome], None] | None = None,
    output: Callable[[str], None] = print,
) -> RunResult:
    """Process pending rows with the step plan."""
    form = config.form
    if form is None or not form.steps:
        raise RunnerError(
            "this config has no form.steps; add one, or use the mapped "
            "pipeline (setup -> calibrate -> run)"
        )
    if limit is not None and limit < 1:
        raise RunnerError(f"limit must be at least 1, got {limit}")

    selected = list(rows[:limit]) if limit is not None else list(rows)
    if not selected:
        return RunResult()

    logger.info(
        "run: %s row(s) selected, verifier=%s, stop_on_failure=%s",
        len(selected),
        config.confirmation.verifier,
        config.safety.stop_on_failure,
    )

    missing = [
        column
        for column in step_columns(form)
        if column not in selected[0].values
    ]
    if missing:
        raise RunnerError(
            "the step plan references column(s) missing from the workbook: "
            + ", ".join(repr(name) for name in missing)
        )

    outcomes: list[RowOutcome] = []
    stopped = False
    stopped_row: int | None = None
    stop_reason = ""

    for row in selected:
        try:
            outcome = run_form_row(
                row,
                config=config,
                writer=writer,
                confirmation=confirmation,
                controller=controller,
                on_before_save=on_before_save,
                confirm_each=confirm_each,
                input_func=input_func,
                screenshot_dir=screenshot_dir,
                pending_status_path=pending_status_path,
                status_attempts=status_attempts,
                sleep=sleep,
                output=output,
            )
        except RunnerAborted as exc:
            stopped = True
            stopped_row = row.row_number
            stop_reason = str(exc)
            output(f"Stopped: {exc}")
            break

        outcomes.append(outcome)
        if on_row is not None:
            on_row(outcome)
        if not outcome.status_written:
            stopped = True
            stopped_row = row.row_number
            sidecar = (
                Path(pending_status_path)
                if pending_status_path is not None
                else DEFAULT_PENDING_STATUS
            )
            stop_reason = (
                f"row {row.row_number} finished as {outcome.status} but its "
                f"status could not be written; queued in {sidecar}. Fix "
                "workbook access before processing more rows"
            )
            output(f"Stopping: {stop_reason}")
            break
        if (
            outcome.status != config.excel.done_value
            and config.safety.stop_on_failure
        ):
            stopped = True
            stopped_row = row.row_number
            stop_reason = (
                f"row {row.row_number} finished as {outcome.status} and "
                "safety.stop_on_failure is enabled"
            )
            output(f"Stopping: {stop_reason}")
            break

    return RunResult(
        outcomes=tuple(outcomes),
        stopped=stopped,
        stopped_row=stopped_row,
        stop_reason=stop_reason,
    )


__all__ = [
    "BUTTON_CONTROL_TYPES",
    "PlanResult",
    "add_button_labels",
    "column_values",
    "execute_form_steps",
    "run_form_row",
    "run_form_rows",
    "step_columns",
]
