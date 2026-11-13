"""First-row calibration loop (Segment 6).

The approved mapping is trusted only after Gemini approves both the filled
form (pre-save) and the saved result (post-save). The loop re-reads the live
UIA tree on every attempt, fills the row with deterministic input, and never
retries a save that may already have succeeded.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops

from ..ai.client import GeminiClient, GeminiError
from ..ai.mapping import ApprovedMapping, MappingError, mark_trusted
from ..ai.verification import check_post_save, check_pre_save
from ..config import AgentConfig, project_root
from ..desktop.capture import Capture, CaptureError, capture_window
from ..desktop.coordinates import rect_within
from ..desktop.input import (
    VK_A,
    VK_CONTROL,
    VK_DELETE,
    InputError,
    click,
    paste_text,
    press_chord,
    press_key,
)
from ..desktop.uia import UIAError, UIAInspection, inspect_window
from ..desktop.window import WindowError, attach_window
from ..excel.reader import ExcelRow, format_value
from ..excel.writer import ExcelWriteError, WorkbookWriter
from ..models.screen import Rect
from ..models.verification import VerificationError
from ..safety.controller import SafetyController

logger = logging.getLogger(__name__)

_EDITABLE_TYPES = {"Edit", "Document", "Spinner"}


class CalibrationError(RuntimeError):
    """The calibration row cannot be processed."""


class CalibrationAborted(CalibrationError):
    """The user stopped the agent before the row was saved."""


@dataclass(frozen=True)
class ResolvedTarget:
    """One field or button resolved against the live window."""

    key: str
    rect: Rect
    candidate_id: str | None
    source: str
    field_type: str = "text"


@dataclass(frozen=True)
class ResolvedLayout:
    """The live rectangles for one calibration attempt."""

    fields: dict[str, ResolvedTarget]
    save: ResolvedTarget | None
    warnings: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class SaveEvidence:
    """The deterministic before/after comparison of the filled fields."""

    unchanged: bool
    compared: tuple[str, ...] = ()
    identical: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class CalibrationResult:
    """The outcome of one calibration row."""

    row_number: int
    status: str
    attempts: int
    trusted: bool
    error: str = ""
    screenshots: tuple[Path, ...] = ()


def _normalise(text: str) -> str:
    return " ".join(str(text).split())


def _same_name(left: str, right: str) -> bool:
    return _normalise(left).casefold() == _normalise(right).casefold()


def _inside(rect: Rect, window: Rect) -> bool:
    return rect_within(rect, window, tolerance=2)


def _pick_candidate(
    inspection: UIAInspection,
    *,
    candidate_id: str | None,
    label: str,
    control_types: set[str],
) -> tuple[ResolvedTarget | None, str | None]:
    """Find a live control by id (when the name agrees), then by name."""
    if not candidate_id and not label:
        return None, "has neither a UIA id nor a label"
    by_id = {item.candidate_id: item for item in inspection.candidates}
    window = inspection.window.rect

    candidate = by_id.get(candidate_id) if candidate_id else None
    warning: str | None = None
    if candidate is not None:
        name_ok = not label or _same_name(candidate.name, label)
        if name_ok and candidate.control_type in control_types and _inside(
            candidate.rect, window
        ):
            return (
                ResolvedTarget(
                    key="",
                    rect=candidate.rect,
                    candidate_id=candidate.candidate_id,
                    source="uia-id",
                ),
                None,
            )
        if label and not name_ok:
            warning = (
                f"UIA id {candidate_id} now names {candidate.name!r} instead of "
                f"{label!r}; ignoring it"
            )

    for item in inspection.candidates:
        if item.control_type not in control_types:
            continue
        if label and not _same_name(item.name, label):
            continue
        if not _inside(item.rect, window):
            continue
        return (
            ResolvedTarget(
                key="",
                rect=item.rect,
                candidate_id=item.candidate_id,
                source="uia-name",
            ),
            warning,
        )
    return None, warning


def resolve_layout(
    mapping: ApprovedMapping, inspection: UIAInspection
) -> ResolvedLayout:
    """Resolve every mapped field and the Save button to live rectangles."""
    warnings: list[str] = []
    problems: list[str] = []
    fields: dict[str, ResolvedTarget] = {}
    window = inspection.window.rect

    for entry in mapping.entries:
        target, warning = _pick_candidate(
            inspection,
            candidate_id=entry.uia_candidate_id,
            label=entry.label,
            control_types=_EDITABLE_TYPES,
        )
        if warning:
            warnings.append(f"{entry.column}: {warning}")
        if target is None:
            if _inside(entry.box, window):
                target = ResolvedTarget(
                    key=entry.column,
                    rect=entry.box,
                    candidate_id=None,
                    source="mapping",
                    field_type=entry.type,
                )
                warnings.append(
                    f"{entry.column}: no live UIA control matched; using the "
                    "saved rectangle"
                )
            else:
                problems.append(
                    f"{entry.column}: the saved rectangle {entry.rect} is "
                    f"outside the current window {window.bbox}; re-run setup"
                )
                continue
        fields[entry.column] = ResolvedTarget(
            key=entry.column,
            rect=target.rect,
            candidate_id=target.candidate_id,
            source=target.source,
            field_type=entry.type,
        )

    save: ResolvedTarget | None = None
    button = mapping.button_for_role("save")
    if button is None:
        problems.append("the approved mapping has no button with role 'save'")
    else:
        target, warning = _pick_candidate(
            inspection,
            candidate_id=button.uia_candidate_id,
            label=button.label,
            control_types={"Button"},
        )
        if warning:
            warnings.append(f"Save: {warning}")
        if target is None:
            if _inside(button.box, window):
                target = ResolvedTarget(
                    key="save",
                    rect=button.box,
                    candidate_id=None,
                    source="mapping",
                )
                warnings.append(
                    "Save: no live UIA button matched; using the saved rectangle"
                )
            else:
                problems.append(
                    f"Save: the saved rectangle {button.rect} is outside the "
                    f"current window {window.bbox}; re-run setup"
                )
        if target is not None:
            save = ResolvedTarget(
                key="save",
                rect=target.rect,
                candidate_id=target.candidate_id,
                source=target.source,
            )

    return ResolvedLayout(
        fields=fields,
        save=save,
        warnings=tuple(warnings),
        problems=tuple(problems),
    )


def values_match(observed: str, expected: str, field_type: str) -> bool:
    """Compare a control's read-back value with the row value."""
    if field_type == "number":
        try:
            return abs(
                float(str(observed).strip().replace(",", ""))
                - float(str(expected).strip().replace(",", ""))
            ) < 1e-9
        except ValueError:
            return False
    return _normalise(observed) == _normalise(expected)


def read_back(
    inspection: UIAInspection,
    layout: ResolvedLayout,
    values: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Read every live field value back; return ``(problems, warnings)``."""
    problems: list[str] = []
    warnings: list[str] = []
    for column, target in layout.fields.items():
        if target.candidate_id is None:
            continue
        try:
            observed = inspection.read_value(target.candidate_id)
        except UIAError as exc:
            warnings.append(
                f"{column}: could not read the value back ({exc}); "
                "relying on the screenshot check"
            )
            continue
        expected = values.get(column, "")
        if not values_match(observed, expected, target.field_type):
            problems.append(
                f"{column}: read back {observed!r}, expected {expected!r}"
            )
    return problems, warnings


def _crop(capture: Capture, rect: Rect) -> Image.Image | None:
    """Crop a screen rectangle from a capture as grayscale."""
    scale_x = capture.scale_x or 1.0
    scale_y = capture.scale_y or 1.0
    left = round((rect.left - capture.origin[0]) / scale_x)
    top = round((rect.top - capture.origin[1]) / scale_y)
    right = round((rect.right - capture.origin[0]) / scale_x)
    bottom = round((rect.bottom - capture.origin[1]) / scale_y)

    left = max(0, min(capture.image.width - 1, left))
    top = max(0, min(capture.image.height - 1, top))
    right = max(left + 1, min(capture.image.width, right))
    bottom = max(top + 1, min(capture.image.height, bottom))
    if right <= left or bottom <= top:
        return None
    return capture.image.crop((left, top, right, bottom)).convert("L")


def _same_image(left: Image.Image, right: Image.Image, tolerance: int = 12) -> bool:
    if left.size != right.size:
        return False
    difference = ImageChops.difference(left, right)
    mask = difference.point(lambda value: 255 if value > tolerance else 0)
    return mask.getbbox() is None


def compare_saved_fields(
    pre: Capture,
    post: Capture,
    layout: ResolvedLayout,
    values: dict[str, str],
    tolerance: int = 12,
) -> SaveEvidence:
    """Detect whether the filled fields are pixel-identical after Save."""
    if pre.image.size != post.image.size:
        return SaveEvidence(
            unchanged=False,
            detail="the window size changed between captures",
        )

    compared: list[str] = []
    identical: list[str] = []
    for column, target in layout.fields.items():
        if not values.get(column, "").strip():
            continue
        before = _crop(pre, target.rect)
        after = _crop(post, target.rect)
        if before is None or after is None:
            continue
        compared.append(column)
        if _same_image(before, after, tolerance):
            identical.append(column)

    unchanged = bool(compared) and len(identical) == len(compared)
    if not compared:
        detail = "no non-empty field could be compared"
    elif unchanged:
        detail = "the filled fields are unchanged"
    else:
        detail = "the filled fields changed"
    return SaveEvidence(
        unchanged=unchanged,
        compared=tuple(compared),
        identical=tuple(identical),
        detail=detail,
    )


def _running(controller: SafetyController | None) -> bool:
    return controller is None or controller.check()


def _require_running(
    controller: SafetyController | None, message: str
) -> None:
    if not _running(controller):
        raise CalibrationAborted(message)


def _wait(controller: SafetyController | None, seconds: float) -> bool:
    if seconds <= 0:
        return controller is None or controller.check()
    if controller is not None:
        return controller.wait(seconds)
    time.sleep(seconds)
    return True


def fill_row(
    layout: ResolvedLayout,
    values: dict[str, str],
    *,
    controller: SafetyController | None = None,
    delay: float = 0.0,
) -> None:
    """Fill every mapped field with a click, select-all, and paste/clear."""
    for column, target in layout.fields.items():
        _require_running(controller, "stopped while filling the form")
        x, y = target.rect.center
        click(x, y)
        if not _wait(controller, delay):
            raise CalibrationAborted("stopped while filling the form")
        press_chord(VK_CONTROL, VK_A)
        value = values.get(column, "")
        if value:
            paste_text(value)
        else:
            press_key(VK_DELETE)
        if not _wait(controller, delay):
            raise CalibrationAborted("stopped while filling the form")


def _save_screenshot(
    capture: Capture,
    directory: Path,
    row_number: int,
    attempt: int,
    phase: str,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = (
        directory
        / f"calibrate_{stamp}_row{row_number}_attempt{attempt}_{phase}.png"
    )
    directory.mkdir(parents=True, exist_ok=True)
    capture.image.save(path)
    return path


def _click_save(layout: ResolvedLayout) -> None:
    if layout.save is None:
        raise CalibrationError("the Save button could not be resolved")
    x, y = layout.save.rect.center
    click(x, y)


def _heartbeat(
    controller: SafetyController | None,
) -> Callable[[int], None]:
    def beat(_attempt: int) -> None:
        if controller is not None:
            controller.check()

    return beat


def _persist_trust(
    mapping: ApprovedMapping,
    mapping_path: str | Path | None,
    trusted: bool,
    output: Callable[[str], None],
) -> bool:
    try:
        mark_trusted(mapping, mapping_path, trusted=trusted)
    except MappingError as exc:
        output(f"WARNING: could not persist trusted={trusted}: {exc}")
        return False
    return trusted


def _write_status(
    writer: WorkbookWriter,
    row: ExcelRow,
    status: str,
    error: str = "",
) -> None:
    try:
        writer.write_status(row.row_number, status, error=error)
    except ExcelWriteError as exc:
        raise CalibrationError(
            f"could not write {status!r} for row {row.row_number}: {exc}"
        ) from exc


def _recheck(
    row: ExcelRow,
    config: AgentConfig,
    writer: WorkbookWriter,
    mapping: ApprovedMapping,
    mapping_path: str | Path | None,
    *,
    error: str,
    attempts: int,
    screenshots: list[Path],
    output: Callable[[str], None],
) -> CalibrationResult:
    _persist_trust(mapping, mapping_path, False, output)
    _write_status(writer, row, config.excel.recheck_value, error)
    output(f"Row {row.row_number}: {config.excel.recheck_value} - {error}")
    return CalibrationResult(
        row_number=row.row_number,
        status=config.excel.recheck_value,
        attempts=attempts,
        trusted=False,
        error=error,
        screenshots=tuple(screenshots),
    )


def calibrate_row(
    row: ExcelRow,
    *,
    config: AgentConfig,
    mapping: ApprovedMapping,
    writer: WorkbookWriter,
    client: GeminiClient,
    mapping_path: str | Path | None = None,
    controller: SafetyController | None = None,
    screenshot_dir: str | Path | None = None,
    output: Callable[[str], None] = print,
) -> CalibrationResult:
    """Fill, verify, save, and verify one calibration row."""
    if not mapping.entries:
        raise CalibrationError("the approved mapping has no column -> field entries")
    if mapping.button_for_role("save") is None:
        raise CalibrationError("the approved mapping has no Save button")

    missing = [
        entry.column
        for entry in mapping.entries
        if entry.column not in row.values
    ]
    if missing:
        raise CalibrationError(
            "the mapping references column(s) missing from the workbook: "
            + ", ".join(repr(name) for name in missing)
        )

    try:
        values = {
            entry.column: format_value(
                row.values.get(entry.column), entry.type
            )
            for entry in mapping.entries
        }
    except ValueError as exc:
        return _recheck(
            row,
            config,
            writer,
            mapping,
            mapping_path,
            error=f"row {row.row_number} has an invalid value: {exc}",
            attempts=0,
            screenshots=[],
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

        try:
            window = attach_window(config.app)
            inspection = inspect_window(window)
            layout = resolve_layout(mapping, inspection)
            for warning in layout.warnings:
                output(f"  note: {warning}")
            if layout.problems:
                raise CalibrationError("; ".join(layout.problems))

            fill_row(
                layout,
                values,
                controller=controller,
                delay=config.safety.action_delay_seconds,
            )
            if not _wait(controller, 0.2):
                raise CalibrationAborted(
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
            pre_path = _save_screenshot(
                pre, directory, row.row_number, attempt, "pre_save"
            )
            screenshots.append(pre_path)
            output(f"  pre-save screenshot: {pre_path}")

            pre_verdict = check_pre_save(
                client,
                pre,
                mapping.entries,
                values,
                config.gemini,
                on_attempt=_heartbeat(controller),
            )
            if not pre_verdict.approved:
                message = "; ".join(pre_verdict.issues) or "no reason given"
                issues.append(f"attempt {attempt}: pre-save rejected: {message}")
                output(f"  pre-save rejected: {message}")
                _wait(controller, 0.5)
                continue

            _require_running(
                controller, "stopped before Save; the row was not saved"
            )
            _click_save(layout)
            saved = True

            if not _wait(controller, config.actions.wait_after_save_seconds):
                return _recheck(
                    row,
                    config,
                    writer,
                    mapping,
                    mapping_path,
                    error=(
                        "stopped after Save; the record may already exist"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            post = capture_window(window)
            post_path = _save_screenshot(
                post, directory, row.row_number, attempt, "post_save"
            )
            screenshots.append(post_path)
            evidence = compare_saved_fields(pre, post, layout, values)
            output(f"  post-save screenshot: {post_path} ({evidence.detail})")

            post_verdict = check_post_save(
                client,
                pre,
                post,
                mapping.entries,
                values,
                config.gemini,
                on_attempt=_heartbeat(controller),
            )
            if not _running(controller):
                return _recheck(
                    row,
                    config,
                    writer,
                    mapping,
                    mapping_path,
                    error=(
                        "stopped during post-save verification; the record "
                        "may already exist"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            if post_verdict.approved and not evidence.unchanged:
                _write_status(writer, row, config.excel.done_value)
                trusted = _persist_trust(
                    mapping, mapping_path, True, output
                )
                output(
                    f"Row {row.row_number}: {config.excel.done_value} "
                    f"(map trusted={trusted})"
                )
                return CalibrationResult(
                    row_number=row.row_number,
                    status=config.excel.done_value,
                    attempts=attempt,
                    trusted=trusted,
                    screenshots=tuple(screenshots),
                )

            if evidence.unchanged:
                message = "; ".join(post_verdict.issues) or (
                    "the filled fields are unchanged"
                )
                return _recheck(
                    row,
                    config,
                    writer,
                    mapping,
                    mapping_path,
                    error=(
                        "the filled fields are unchanged after Save; the "
                        "record may already exist, so the row was not saved "
                        f"again: {message}"
                    ),
                    attempts=attempt,
                    screenshots=screenshots,
                    output=output,
                )

            message = "; ".join(post_verdict.issues) or "no reason given"
            return _recheck(
                row,
                config,
                writer,
                mapping,
                mapping_path,
                error=(
                    "post-save check uncertain; the record may already exist: "
                    f"{message}"
                ),
                attempts=attempt,
                screenshots=screenshots,
                output=output,
            )

        except CalibrationAborted:
            raise
        except (
            GeminiError,
            VerificationError,
            UIAError,
            CaptureError,
            InputError,
            WindowError,
            CalibrationError,
        ) as exc:
            if saved:
                return _recheck(
                    row,
                    config,
                    writer,
                    mapping,
                    mapping_path,
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
        f"calibration failed after {attempts} attempt(s): "
        + " | ".join(issues[-4:])
    )
    return _recheck(
        row,
        config,
        writer,
        mapping,
        mapping_path,
        error=error,
        attempts=attempts,
        screenshots=screenshots,
        output=output,
    )


__all__ = [
    "CalibrationAborted",
    "CalibrationError",
    "CalibrationResult",
    "ResolvedLayout",
    "ResolvedTarget",
    "SaveEvidence",
    "calibrate_row",
    "compare_saved_fields",
    "fill_row",
    "read_back",
    "resolve_layout",
    "values_match",
]
