"""Interactive setup and automation wizard (Segment 9+ / python main.py --agent).

Guides the user through:
1. Detecting or selecting their target application window.
2. Locating and validating their Excel input workbook.
3. Generating a custom configuration file.
4. Testing window inspection and Excel reading.
5. Visual layout grounding and mapping approval with Gemini.
6. First-row calibration with pre/post-save checks.
7. Final confirmation before launching batch automation.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..config import (
    AgentConfig,
    ConfigError,
    load_config,
    load_env,
    project_root,
    resolve_path,
)
from ..desktop.window import WindowError, find_window, focus_window, list_windows, pin_window
from ..excel.reader import format_value, pending_rows, read_workbook
from ..logging_setup import mask_secret


def _prompt(text: str, default: str | None = None) -> str:
    """Prompt the user for input, displaying an optional default."""
    if default is not None and default != "":
        display = f"{text} [{default}]: "
    else:
        display = f"{text}: "
    try:
        raw = input(display).strip()
    except (KeyboardInterrupt, EOFError):
        print("\nWizard cancelled.")
        sys.exit(0)
    if not raw and default is not None:
        return default
    return raw


def _prompt_yes_no(question: str, default_yes: bool = True) -> bool:
    """Prompt for a yes/no answer."""
    hint = "[Y/n]" if default_yes else "[y/N]"
    try:
        raw = input(f"{question} {hint}: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nWizard cancelled.")
        sys.exit(0)
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _clean_path(path_str: str) -> str:
    """Clean quoted or drag-and-dropped file paths."""
    clean = path_str.strip()
    if (clean.startswith('"') and clean.endswith('"')) or (
        clean.startswith("'") and clean.endswith("'")
    ):
        clean = clean[1:-1]
    return clean.strip()


def _select_window() -> tuple[str, str | None]:
    """Interactively select or search for an open target window."""
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 1: Target Application Window")
    print("------------------------------------------------------------------------")
    print("Open windows detected on your desktop:")

    all_windows = list_windows()
    # Filter out system and utility windows that aren't target apps
    filtered = [
        w
        for w in all_windows
        if w.title
        and w.process_name.lower()
        not in ("explorer.exe", "shellexperiencehost.exe", "searchhost.exe")
    ]

    for idx, win in enumerate(filtered[:10], start=1):
        print(f"  [{idx:2d}] {win.title} ({win.process_name})")

    print()
    print("Type a number from the list above, or enter text to search by title.")
    while True:
        entry = _prompt("Window selection")
        if not entry:
            continue

        if entry.isdigit() and 1 <= int(entry) <= len(filtered):
            selected = filtered[int(entry) - 1]
            focus_window(selected)
            pin_window(selected.handle, True)
            print(f"[ OK ] Selected & pinned window in front: {selected.title!r} ({selected.process_name})")
            return selected.title, selected.process_name

        try:
            matched = find_window(title_contains=entry)
            focus_window(matched)
            pin_window(matched.handle, True)
            print(f"[ OK ] Found & pinned window in front: {matched.title!r} ({matched.process_name})")
            return matched.title, matched.process_name
        except WindowError as exc:
            print(f"Window search failed: {exc}")
            print("Please try again, or make sure your application is open and visible.")


def _select_excel() -> tuple[str, str, str, list[str]]:
    """Prompt for Excel file, select sheet, and identify key columns."""
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 2: Excel Input Workbook")
    print("------------------------------------------------------------------------")

    while True:
        raw_path = _prompt("Enter path to your Excel file (.xlsx)")
        excel_path = _clean_path(raw_path)
        p = Path(excel_path)
        if not p.is_file():
            # Check relative to workspace
            p_rel = project_root() / excel_path
            if p_rel.is_file():
                excel_path = str(p_rel.resolve())
                break
            print(f"File not found: {raw_path!r}. Please enter a valid path.")
            continue
        excel_path = str(p.resolve())
        break

    # Load workbook to inspect sheets and headers
    try:
        wb = load_workbook(excel_path, read_only=True, data_only=True)
        sheet_names = wb.sheetnames
    except Exception as exc:
        print(f"Could not open Excel file: {exc}")
        sys.exit(1)

    if len(sheet_names) == 1:
        sheet_name = sheet_names[0]
        print(f"[ OK ] Using sheet: {sheet_name!r}")
    else:
        print(f"Available sheets: {', '.join(sheet_names)}")
        sheet_name = _prompt("Select sheet", default=sheet_names[0])
        if sheet_name not in sheet_names:
            sheet_name = sheet_names[0]

    # Read column headers from row 1
    sheet = wb[sheet_name]
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    wb.close()

    if not header_row:
        print("Error: Sheet has no header row.")
        sys.exit(1)

    headers = [str(cell).strip() for cell in header_row if cell is not None and str(cell).strip()]
    print(f"[ OK ] Found {len(headers)} column header(s):")
    for idx, col in enumerate(headers, start=1):
        print(f"   - {col}")

    # Prompt for unique ID column
    default_id = headers[0] if headers else "ID"
    id_column = _prompt("Enter unique ID column name", default=default_id)
    if id_column not in headers:
        print(f"Note: '{id_column}' was not in the detected headers list; using it anyway.")

    return excel_path, sheet_name, id_column, headers


def _select_mode() -> str:
    """Choose between the AI mapping pipeline and described multi steps."""
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 3: How Should the Agent Work?")
    print("------------------------------------------------------------------------")
    print("  [1] Single step - AI mapping + calibration (default)")
    print("      Gemini finds the fields on screen, matches your columns to them,")
    print("      and proves the map on the first row. Needs an API key and two")
    print("      extra steps.")
    print("  [2] Multi step - describe each step in your own words")
    print("      Say \"click Add New\", \"fill up all the form\". The agent does")
    print("      each one on the first row while you watch, then repeats it for")
    print("      every row. No plan typing, no mapping. No dropdowns yet.")
    print()
    choice = _prompt("Choose 1 or 2", default="1").strip().lower()
    return "steps" if choice.startswith("2") or choice.startswith("s") else "mapping"


def _inspect_live(app_title: str) -> tuple[Any, Any] | None:
    """Return ``(window, inspection)`` for the target window, or ``None``."""
    from ..desktop.uia import UIAError, inspect_window

    try:
        window = find_window(title_contains=app_title)
        inspection = inspect_window(window, max_elements=600)
    except (WindowError, UIAError) as exc:
        print(f"[WARN] Could not read the window's controls: {exc}")
        return None
    # inspect_window restores a minimized window, so hand back the geometry it
    # actually read instead of the snapshot taken before.
    return inspection.window, inspection


def _step_session_dir() -> Path:
    """Where the screenshots of one setup session are kept."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return project_root() / "data" / "steps" / stamp


def _save_shot(capture: Any, directory: Path, name: str) -> Path:
    """Save one screenshot and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    capture.image.save(path)
    return path


def _describe_step(step: dict) -> str:
    """One line that names the work a step does."""
    if step.get("action") == "click":
        return f"click {step.get('button', '')!r}"
    label = step.get("field") or step.get("column", "")
    return f"fill {label!r} from column {step.get('column', '')!r}"


def _form_steps(plan: Any) -> list[Any]:
    """Turn a resolved plan into the runner's own step models."""
    from ..models.actions import ClickStep, FillStep

    if plan.kind == "click":
        return [ClickStep(action="click", button=plan.label)]
    return [
        FillStep(action="fill", column=fill.column, field=fill.label)
        for fill in plan.fills
    ]


def _print_step_plan(
    plan: Any,
    *,
    index: int,
    instruction: str,
    screenshot: Path,
    headers: list[str],
    types: dict[str, str],
) -> None:
    """Show what the agent understood, before it touches the screen."""
    print()
    print("-" * 72)
    print(f"  Step {index}: {instruction}")
    print(f"  Screen   : {screenshot}")
    print("  I will do this, in order:")
    if plan.kind == "click":
        print(f"    1. click {plan.label!r}")
    else:
        for position, fill in enumerate(plan.fills, start=1):
            detail = types.get(fill.column, "?")
            if fill.confidence:
                detail += f", {fill.confidence:.2f}"
            print(
                f"    {position}. fill {fill.label!r} <- column "
                f"{fill.column!r} ({detail})"
            )
        touched = set(plan.columns)
        other = [
            header
            for header in headers
            if header not in touched
            and not header.lower().startswith("agent ")
        ]
        if other:
            print("  Not filled by this step: " + ", ".join(other))
    print("-" * 72)


def _review_step_plan(problem: str | None = None) -> tuple[str, str]:
    """Ask what to do with the resolved step.

    Returns ``("done", "")`` to run it now, ``("redo", "")`` to look at the
    screen again, or ``("edit", correction)`` when the user said what is wrong.
    With a *problem*, the Enter default becomes ``redo``.
    """
    if problem:
        print(f"  {problem}")
    print()
    print("  [Enter] done - do it now on the first row")
    print("  [r]     redo - look at the screen again")
    print("  or type what is wrong, in your words")
    answer = _prompt("Your answer", default="").strip()
    lowered = answer.casefold()
    if lowered in ("", "d", "done"):
        return ("redo" if problem else "done"), ""
    if lowered in ("r", "redo"):
        return "redo", ""
    return "edit", answer


def _infer_column_type(
    excel_path: str, sheet_name: str, column: str, header_row: int = 1
) -> str:
    """Return 'number' when every filled cell in the column parses as one."""
    try:
        wb = load_workbook(excel_path, read_only=True, data_only=True)
    except Exception:
        return "text"
    try:
        sheet = wb[sheet_name]
        names: list[str] = []
        rows: list[tuple] = []
        for index, row in enumerate(
            sheet.iter_rows(min_row=header_row, values_only=True)
        ):
            if index == 0:
                names = [
                    str(cell).strip() if cell is not None else ""
                    for cell in row
                ]
                continue
            if index > 40:
                break
            rows.append(row)
        if column not in names:
            return "text"
        position = names.index(column)
        seen = 0
        for row in rows:
            if position >= len(row):
                continue
            value = row[position]
            if value is None or str(value).strip() == "":
                continue
            seen += 1
            try:
                float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                return "text"
        return "number" if seen else "text"
    finally:
        wb.close()


def _ask_confirmation() -> dict[str, Any]:
    """Ask what a successful save looks like, in plain language."""
    print()
    print("How does your app show that a save worked?")
    print("  Type a word or phrase it displays (e.g. Saved, Success,")
    print("  Record created). Leave it blank if it shows nothing - then each")
    print("  Save is checked by looking for the new row instead.")
    text = _prompt("Success text", default="")
    cleaned = text.strip()
    return {
        "success_region": None,
        "error_region": None,
        "success_pattern": f"(?i){re.escape(cleaned)}" if cleaned else "",
        "error_patterns": [
            "(?i)required",
            "(?i)invalid",
            "(?i)error",
        ]
        if cleaned
        else [],
    }


def _ask_save_check() -> str:
    """Ask how every Save after the first row should be checked.

    Returns ``"row"`` (local check), ``"text"`` (AI reads the screen) or
    ``"none"`` (no check at all - the rows are just filled and saved).
    """
    print()
    print("Check the screen after every Save?")
    print("  [y] yes - each Save is checked")
    print("  [n] no - fill and save every row without checking")
    if not _prompt_yes_no("Check the screen after each Save", default_yes=True):
        return "none"
    print()
    print("Check every Save with AI? Gemini reads the screen after each Save.")
    print("  [y] AI reads each screen")
    print("  [n] check the new row locally (no AI, no API cost)")
    return "text" if _prompt_yes_no("Use AI for every row", default_yes=False) else "row"


def _footprint_from_model(
    client: Any, image: Any, values: dict[str, str], columns: list[str]
) -> list[str]:
    """Ask the model, once, which typed value shows up after a save."""
    from ..ai.prompts import load_prompt, render_prompt

    pairs = [
        (column, values[column].strip())
        for column in columns
        if values.get(column, "").strip()
    ]
    if client is None or image is None or not pairs:
        return []
    listed = "\n".join(f"- {column}: {value}" for column, value in pairs)
    prompt = render_prompt(load_prompt("footprint"), {"CANDIDATES": listed})
    try:
        response = client.generate_json(prompt, image)
    except Exception as exc:  # the local check still works without an answer
        print(f"  note: the model could not pick a value to look for ({exc})")
        return []
    data = getattr(response, "data", None)
    chosen = data.get("column") if isinstance(data, dict) else None
    if isinstance(chosen, str) and chosen.strip() in columns:
        return [chosen.strip()]
    return []


def _apply_save_check(
    config_file: Path, verifier: str, footprint: list[str]
) -> bool:
    """Write the chosen Save check into the generated config file."""
    try:
        data = json.loads(Path(config_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[WARN] Could not update {config_file}: {exc}")
        return False
    confirmation = data.setdefault("confirmation", {})
    confirmation["verifier"] = verifier
    confirmation["footprint"] = list(footprint)
    try:
        Path(config_file).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[WARN] Could not write {config_file}: {exc}")
        return False
    return True


def _config_data(
    app_title: str,
    process_name: str | None,
    excel_path: str,
    sheet_name: str,
    id_column: str,
    headers: list[str],
    save_button: str,
    *,
    form: dict[str, Any] | None = None,
    fields: list[dict] | None = None,
) -> dict[str, Any]:
    """Build the configuration a wizard run would write."""
    p = Path(excel_path)
    output_path = f"data/output/{p.stem}_agent{p.suffix}"

    field_configs = fields if fields is not None else [
        {"column": col, "field": col, "type": "text"}
        for col in headers
        if not col.lower().startswith("agent ")
    ]

    config_data: dict[str, Any] = {
        "app": {
            "title_contains": app_title,
            "process_name": process_name,
            "require_foreground": True,
            "pin_window": True,
        },
        "excel": {
            "input_path": excel_path,
            "output_path": output_path,
            "sheet": sheet_name,
            "header_row": 1,
            "id_column": id_column,
            "status_column": "Agent Status",
            "timestamp_column": "Agent Timestamp",
            "error_column": "Agent Error",
            "done_value": "DONE",
            "recheck_value": "RECHECK",
            "skip_statuses": ["DONE", "RECHECK"],
            "empty_cell_policy": "skip",
            "required_columns": [id_column],
        },
        "fields": field_configs,
        "actions": {
            "save_button": save_button,
            "new_record_button": None,
            "wait_after_save_seconds": 1.0,
        },
        "gemini": {
            "model": "gemini-3.6-flash",
            "max_image_dimension": 1600,
            "timeout_seconds": 60,
            "max_retries": 3,
            "max_ai_calls": 50,
            "min_layout_confidence": 0.6,
            "min_mapping_confidence": 0.6,
        },
        "confirmation": {
            "success_region": None,
            "error_region": None,
            "success_pattern": "",
            "error_patterns": [],
        },
        "safety": {
            "stop_hotkey": "alt+t",
            "pause_hotkey": "alt+p",
            "resume_hotkey": "alt+r",
            "block_external_input": True,
            "max_attempts": 3,
            "action_delay_seconds": 0.15,
            "stop_on_failure": True,
        },
    }

    if form is not None:
        config_data["form"] = form
    return config_data


def _preview_config(
    app_title: str,
    process_name: str | None,
    excel_path: str,
    sheet_name: str,
    id_column: str,
) -> AgentConfig:
    """The Excel settings the workbook is read with, before any step exists."""
    return AgentConfig.model_validate(
        _config_data(
            app_title=app_title,
            process_name=process_name,
            excel_path=excel_path,
            sheet_name=sheet_name,
            id_column=id_column,
            headers=[],
            save_button="Save",
            fields=[],
        )
    )


def _generate_config(
    app_title: str,
    process_name: str | None,
    excel_path: str,
    sheet_name: str,
    id_column: str,
    headers: list[str],
    save_button: str,
    *,
    form: dict[str, Any] | None = None,
    fields: list[dict] | None = None,
    confirmation: dict[str, Any] | None = None,
    filename: str = "agent.interactive.json",
) -> Path:
    """Generate and write a customized configuration file."""
    config_dir = project_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / filename

    config_data = _config_data(
        app_title=app_title,
        process_name=process_name,
        excel_path=excel_path,
        sheet_name=sheet_name,
        id_column=id_column,
        headers=headers,
        save_button=save_button,
        form=form,
        fields=fields,
    )
    if confirmation is not None:
        config_data["confirmation"] = confirmation

    config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
    return config_path


@dataclass(frozen=True)
class SaveOutcome:
    """One proved Save, plus the evidence later rows can be checked against."""

    status: str = ""
    before: Any = None
    after: Any = None
    after_image: Any = None
    values: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.status)


def _save_live_row(
    *,
    config: AgentConfig,
    row_number: int,
    values: dict[str, str],
    fields: dict[str, Any],
    window: Any,
    session: Path,
) -> SaveOutcome:
    """Click Save on the form the steps already filled, and record the row.

    Returns what the Save left behind - the status written for the row and the
    screens before and after - or a falsy :class:`SaveOutcome` when Save could
    not be reached at all. Those screens let the wizard decide, once, which
    value later saves can be checked against without a model call.
    """
    from ..ai.client import GeminiClient, GeminiError
    from ..desktop.capture import CaptureError, capture_window
    from ..desktop.input import click
    from ..desktop.uia import UIAError, inspect_window
    from ..excel.writer import ExcelWriteError, WorkbookWriter
    from .calibration import ResolvedLayout, compare_saved_fields, resolve_page_target
    from .confirmation import (
        CONFIRMED,
        UNKNOWN,
        ConfirmationError,
        GeminiTextReader,
        TextConfirmation,
    )
    from .form_steps import BUTTON_CONTROL_TYPES, add_button_labels
    from .row_evidence import check_row_appeared
    from .row_search import find_appended_row
    from .pending_status import (
        DEFAULT_PENDING_STATUS,
        PendingStatusError,
        queue_pending_status,
        write_status_with_retries,
    )
    from .workspace import WorkspaceError, open_workspace

    try:
        # Wide read: the table rows are the evidence later saves are checked
        # against, and browsers expose them only as DataItem controls.
        inspection = inspect_window(window, include_data_items=True)
    except UIAError as exc:
        print(f"[WARN] Could not read the window before Save: {exc}")
        return SaveOutcome()

    save, warning = resolve_page_target(
        inspection,
        label=config.actions.save_button,
        control_types=BUTTON_CONTROL_TYPES,
    )
    if warning:
        print(f"  note: Save: {warning}")
    if save is None:
        print(
            f"[WARN] The Save button {config.actions.save_button!r} was not "
            "found in the live window; nothing was clicked."
        )
        return SaveOutcome()

    layout = ResolvedLayout(fields=dict(fields), save=save)
    try:
        pre = capture_window(window)
    except CaptureError as exc:
        print(f"[WARN] {exc}")
        return SaveOutcome()
    _save_shot(pre, session, f"row{row_number}-pre-save.png")

    x, y = save.rect.center
    click(x, y)
    time.sleep(config.actions.wait_after_save_seconds)

    try:
        post = capture_window(window)
    except CaptureError as exc:
        print(f"[WARN] The row was saved but the screen could not be read: {exc}")
        return SaveOutcome()
    _save_shot(post, session, f"row{row_number}-post-save.png")

    try:
        after_inspection = inspect_window(window, include_data_items=True)
    except UIAError as exc:
        print(f"  note: the screen could not be read after Save: {exc}")
        after_inspection = None

    def proven(status: str) -> SaveOutcome:
        return SaveOutcome(
            status=status,
            before=inspection,
            after=after_inspection,
            after_image=post.image,
            values=dict(values),
        )

    evidence = compare_saved_fields(pre, post, layout, values)
    print(f"  save hint: {evidence.detail}")

    status = config.excel.done_value
    error = ""
    result = None
    # A success message, where the app has one, is the strongest evidence.
    if config.confirmation.success_pattern or config.confirmation.error_patterns:
        try:
            confirmation = TextConfirmation(
                config.confirmation, GeminiTextReader(GeminiClient(config.gemini))
            )
            result = confirmation.confirm(post.image, weak_hint=evidence.detail)
            print(f"  save confirmation: {result.outcome} ({result.reason})")
        except (ConfirmationError, GeminiError) as exc:
            print(f"  the success text could not be read: {exc}")
    # An app that says nothing still shows the row, so judge the screen - with
    # no model call. This is the same check every later row gets, which is why
    # the first row can no longer be the one that fails for lack of a message.
    if result is None or result.outcome == UNKNOWN:
        local = check_row_appeared(
            inspection,
            after_inspection,
            values,
            list(values),
            config.confirmation.error_patterns,
        )
        # The first row can land below the fold as well, so it gets the same
        # downward search every later row gets.
        if (
            local.outcome != CONFIRMED
            and after_inspection is not None
            and config.scrolling.enabled
        ):
            search = find_appended_row(
                after_inspection,
                window=window,
                before=inspection,
                values=values,
                footprint=list(values),
                error_patterns=config.confirmation.error_patterns,
                anchor_labels=add_button_labels(config.form) if config.form else [],
                max_screens=config.scrolling.max_screens,
                settle_seconds=config.scrolling.settle_seconds,
                wheel_notches=config.scrolling.wheel_notches,
                note=print,
            )
            if search.fatal:
                print(f"[WARN] {search.fatal}")
            local = search.result
        if result is None or local.outcome != UNKNOWN:
            print(f"  save confirmation: {local.outcome} ({local.reason})")
        result = local
    if result is not None and result.outcome != CONFIRMED:
        status = config.excel.recheck_value
        error = result.reason or "the save could not be confirmed"

    try:
        workspace = open_workspace(config)
        writer = WorkbookWriter.from_config(config, path=workspace.path)
    except (ExcelWriteError, WorkspaceError) as exc:
        print(f"[WARN] {exc}")
        return proven(status)

    try:
        ok, failure = write_status_with_retries(
            writer.write_status,
            row_number,
            status,
            error,
            attempts=3,
            sleep=time.sleep,
        )
        if ok:
            line = f"Row {row_number}: {status}"
            if error:
                line += f" - {error}"
            print(line)
        else:
            try:
                queue_pending_status(
                    row_number=row_number,
                    status=status,
                    error=error,
                    path=DEFAULT_PENDING_STATUS,
                )
            except PendingStatusError:
                pass
            print(
                f"[WARN] The status could not be written ({failure}); "
                f"queued in {DEFAULT_PENDING_STATUS}"
            )
    finally:
        writer.close()
    return proven(status)


def _run_multi_step_wizard(
    app_title: str,
    process_name: str | None,
    excel_path: str,
    sheet_name: str,
    id_column: str,
    headers: list[str],
    env_path: str | None,
) -> int:
    """Describe each step in plain words; the agent proves it on the first row."""
    from ..ai.client import GeminiClient, GeminiError
    from ..ai.step_planner import StepPlanError, StepUnsupported, plan_step
    from ..desktop.capture import CaptureError, capture_window
    from ..desktop.uia import UIAError, inspect_window
    from ..excel.reader import ExcelReadError
    from .form_steps import execute_form_steps
    from .row_evidence import decide_footprint

    load_env(env_path)

    print()
    print("------------------------------------------------------------------------")
    print("  STEP 4: Your Steps")
    print("------------------------------------------------------------------------")
    print("Reading the controls this window exposes to Windows UI Automation...")
    live = _inspect_live(app_title)
    if live is None:
        print("Open the app again and re-run the wizard if the window went away.")
        return 1
    window, inspection = live
    print(f"[ OK ] Detected {len(inspection.candidates)} control(s).")
    if len(inspection.candidates) < 3:
        print(
            "[WARN] This window exposes almost no controls. Keep it restored "
            "(not minimized) and in front while you add a step; a browser "
            "only exposes its page controls while the page is drawn."
        )

    base_config = _preview_config(
        app_title, process_name, excel_path, sheet_name, id_column
    )
    try:
        workbook = read_workbook(base_config, excel_path)
    except ExcelReadError as exc:
        print(f"[WARN] {exc}")
        return 1
    pending = pending_rows(workbook, base_config)
    if not pending:
        print("No pending rows were found, so there is nothing to fill.")
        return 1
    row = pending[0]
    print(
        f"Your steps run on row {row.row_number} first, so you can watch "
        "each one before anything is saved."
    )

    session = _step_session_dir()
    steps: list[dict] = []
    labels: dict[str, str] = {}
    types: dict[str, str] = {}
    live_values: dict[str, str] = {}
    filled: dict[str, Any] = {}
    client = None
    pause_after_click = 0.5

    while True:
        print()
        print(f"  Your steps ({len(steps)} so far):")
        if steps:
            for position, step in enumerate(steps, start=1):
                print(f"    {position}. {_describe_step(step)}")
        else:
            print("    (none yet)")
        print("  [1] Add a step")
        print("  [2] Start automation")
        choice = _prompt("Choose 1 or 2", default="1").strip().casefold()

        if choice.startswith("2") or choice.startswith("s"):
            if not steps:
                print("No steps yet, so there is nothing to run.")
                continue
            if not any(step["action"] == "fill" for step in steps):
                print(
                    "No step fills anything yet - add the step that types "
                    "the data."
                )
                continue
            break
        if choice not in ("", "1", "a", "add"):
            print("Enter 1 to add a step, or 2 to start automation.")
            continue

        index = len(steps) + 1
        instruction = _prompt(
            f"Step {index} - what should the agent do here, in your words"
        )
        if not instruction:
            continue
        if client is None:
            try:
                client = GeminiClient(base_config.gemini)
            except GeminiError as exc:
                print(f"[WARN] {exc}")
                return 1

        try:
            capture = capture_window(window)
            inspection = inspect_window(window)
        except CaptureError as exc:
            print(f"[WARN] {exc}")
            continue
        except UIAError as exc:
            print(f"[WARN] Could not read the window's controls: {exc}")
            continue
        window = inspection.window
        shot = _save_shot(capture, session, f"step-{index:02d}-before.png")
        print(f"  Screen saved: {shot}")
        # A click can open a dialog, so the control list has to come from the
        # same moment as the screenshot - not from before the earlier steps.
        print(f"  {len(inspection.candidates)} control(s) on this screen right now.")

        correction = ""
        plan: Any | None = None
        problem = ""
        while True:
            if plan is None:
                try:
                    plan = plan_step(
                        client,
                        capture,
                        inspection.candidates,
                        headers,
                        instruction,
                        base_config.gemini,
                        correction=correction,
                    )
                    problem = ""
                except StepUnsupported as exc:
                    problem = (
                        f"Not supported yet: {exc} Clicking buttons and typing "
                        "into text fields are the only actions in this mode."
                    )
                except (StepPlanError, GeminiError) as exc:
                    problem = str(exc)
            if plan is not None:
                for fill in plan.fills:
                    types.setdefault(
                        fill.column,
                        _infer_column_type(excel_path, sheet_name, fill.column),
                    )
                _print_step_plan(
                    plan,
                    index=index,
                    instruction=instruction,
                    screenshot=shot,
                    headers=headers,
                    types=types,
                )
            action, fix = _review_step_plan(problem or None)
            if action == "done":
                break
            if action == "edit":
                correction = fix
                plan = None
                continue
            try:
                capture = capture_window(window)
                inspection = inspect_window(window)
            except (CaptureError, UIAError) as exc:
                print(f"[WARN] {exc}")
                break
            window = inspection.window
            shot = _save_shot(capture, session, f"step-{index:02d}-before.png")
            print(f"  Screen saved: {shot}")
            print(
                f"  {len(inspection.candidates)} control(s) on this screen "
                "right now."
            )
            plan = None

        if plan is None:
            continue

        try:
            inspection = inspect_window(window)
        except UIAError as exc:
            print(f"[WARN] Could not read the window: {exc}")
            continue

        step_values: dict[str, str] = {}
        bad_value = ""
        for fill in plan.fills:
            column = fill.column
            if column not in types:
                types[column] = _infer_column_type(excel_path, sheet_name, column)
            try:
                step_values[column] = format_value(row.values.get(column), types[column])
            except ValueError as exc:
                bad_value = f"column {column!r}: {exc}"
                break
        if bad_value:
            print(f"  Cannot run this step: row {row.row_number}, {bad_value}")
            continue

        result = execute_form_steps(
            _form_steps(plan),
            step_values,
            window=window,
            inspection=inspection,
            labels={},
            types=types,
            delay=base_config.safety.action_delay_seconds,
            pause_after_click=pause_after_click,
        )
        for note in result.warnings:
            print(f"  note: {note}")
        if result.problems:
            print(f"  The screen did not take that: {'; '.join(result.problems)}")
            print(
                "  The form is left as it is; fix it by hand and add the "
                "step again."
            )
            continue

        filled.update(result.fields)
        live_values.update(step_values)
        if plan.kind == "click":
            steps.append({"action": "click", "button": plan.label})
        else:
            for fill in plan.fills:
                labels.setdefault(fill.column, fill.label)
                steps.append(
                    {
                        "action": "fill",
                        "column": fill.column,
                        "field": fill.label,
                    }
                )
        print(f"[ OK ] Step {index} done on row {row.row_number}.")

    print()
    save_button = _ask_save_button(window, inspection)
    confirmation = _ask_confirmation()
    field_configs = [
        {
            "column": column,
            "field": label,
            "type": types.get(column, "text"),
        }
        for column, label in labels.items()
    ]
    config_file = _generate_config(
        app_title=app_title,
        process_name=process_name,
        excel_path=excel_path,
        sheet_name=sheet_name,
        id_column=id_column,
        headers=headers,
        save_button=save_button,
        form={"steps": steps, "pause_after_click_seconds": pause_after_click},
        fields=field_configs,
        confirmation=confirmation,
        filename="agent.interactive.steps.json",
    )

    print()
    print("=" * 72)
    print("  STEP PLAN READY")
    print("=" * 72)
    for index, step in enumerate(steps, start=1):
        print(f"  {index}. {_describe_step(step)}")
    print(f"  Save button : {save_button}")
    print(f"  Config      : {config_file}")
    print("  Safety      : Alt+T (Stop) | Alt+P (Pause) | Alt+R (Resume)")
    if not confirmation["success_pattern"]:
        print(
            "  Note        : no success text was given, so each Save is "
            "checked by looking for the new row"
        )
    print("=" * 72)

    try:
        config = load_config(str(config_file))
    except ConfigError as exc:
        print(f"[WARN] {exc}")
        return 1

    print()
    print(f"Saving row {row.row_number}, which is already filled...")
    outcome = _save_live_row(
        config=config,
        row_number=row.row_number,
        values=live_values,
        fields=filled,
        window=window,
        session=session,
    )
    if not outcome:
        print("The first row was not saved, so nothing else was started.")
        return 1

    print()
    print(
        f"Row {row.row_number} is {outcome.status}. Later rows replay this plan."
    )

    verifier = _ask_save_check()
    footprint = list(config.confirmation.footprint)
    if verifier == "none":
        # Nothing is checked, so there is nothing to look for: no model call
        # to pick a value, and no screen read for the rows after this one.
        print("  Save check: none - every Save counts as done")
    else:
        footprint = decide_footprint(
            outcome.before, outcome.after, live_values, headers, id_column
        )
        if footprint:
            print(
                "  Save evidence: "
                + ", ".join(
                    f"{live_values.get(column, '')!r} for {column!r}"
                    for column in footprint
                )
                + " is now on the screen."
            )
        else:
            footprint = _footprint_from_model(
                client, outcome.after_image, live_values, headers
            )
            if footprint:
                print(f"  The model picked {footprint[0]!r} to look for.")
            else:
                footprint = [id_column]
                print(
                    "  No new value was visible after Save, so later rows are "
                    f"checked with {id_column!r}."
                )
        if verifier == "text" and not config.confirmation.success_pattern:
            print(
                "  No success text was given earlier, so AI has nothing to match; "
                "checking the row locally instead."
            )
            verifier = "row"
        print(
            "  Save check: "
            + ("AI reads each screen" if verifier == "text" else "local window text")
        )
    if not _apply_save_check(config_file, verifier, footprint):
        return 1
    try:
        config = load_config(str(config_file))
    except ConfigError as exc:
        print(f"[WARN] {exc}")
        return 1
    limit_input = _prompt(
        "Process all remaining rows, or limit count? [Enter for all, or type number]",
        default="",
    )
    limit = (
        int(limit_input)
        if limit_input.isdigit() and int(limit_input) > 0
        else None
    )
    confirm_each = _prompt_yes_no(
        "Pause before every Save click? (recommended the first time)",
        default_yes=True,
    )

    print("\nStarting in 3 seconds... (switch to your app if needed)")
    for count in range(3, 0, -1):
        print(f"  {count}...")
        time.sleep(1.0)

    from ..cli import run_form

    return run_form(
        str(config_file),
        limit=limit,
        confirm_each=confirm_each,
        overlay=True,
        env_path=env_path,
    )


def _ask_save_button(window: Any, inspection: Any) -> str:
    """Ask for the Save button's name, showing the buttons already on screen."""
    from ..desktop.uia import UIAError, inspect_window

    try:
        inspection = inspect_window(window)
    except UIAError as exc:
        print(f"  note: could not re-read the window: {exc}")
    names = [
        candidate.name
        for candidate in inspection.candidates
        if candidate.control_type == "Button" and candidate.name.strip()
    ]
    # A browser window lists its own chrome first, so lead with the buttons
    # that look like Save instead of cutting them off.
    save_like = [name for name in names if "save" in name.casefold()]
    ordered = save_like + [name for name in names if name not in save_like]
    if ordered:
        shown = ", ".join(repr(name) for name in ordered[:8])
        if len(ordered) > 8:
            shown += f", and {len(ordered) - 8} more"
        print(f"  Buttons on this screen: {shown}")
    default = save_like[0] if len(save_like) == 1 else "Save"
    known = {" ".join(name.split()).casefold() for name in names}
    save_button = _prompt("Enter Save button text in your form", default=default)
    while names and " ".join(save_button.split()).casefold() not in known:
        print(f"  No button named {save_button!r} is on this screen.")
        save_button = _prompt(
            "Type the Save button's name exactly as listed above", default=default
        )
    return save_button


def run_wizard(config_path: str | None = None, env_path: str | None = None) -> int:
    """Main interactive wizard workflow."""
    from ..cli import run_batch, run_calibrate, run_setup

    print("=" * 72)
    print("  Excel Desktop Agent - Interactive Setup & Automation Assistant")
    print("=" * 72)

    # 1. Select Target Window 
    app_title, process_name = _select_window()

    # 2. Select Excel Workbook
    excel_path, sheet_name, id_column, headers = _select_excel()

    # 3. Operating mode: AI mapping, or steps described in plain words
    if _select_mode() == "steps":
        return _run_multi_step_wizard(
            app_title,
            process_name,
            excel_path,
            sheet_name,
            id_column,
            headers,
            env_path,
        )

    # 4. Action settings
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 4: Form Actions & Settings")
    print("------------------------------------------------------------------------")
    save_button = _prompt("Enter Save button text in your form", default="Save")

    # 4. Generate configuration
    config_file = _generate_config(
        app_title=app_title,
        process_name=process_name,
        excel_path=excel_path,
        sheet_name=sheet_name,
        id_column=id_column,
        headers=headers,
        save_button=save_button,
    )
    print(f"[ OK ] Configuration generated: {config_file}")

    # 5. Visual Layout Grounding & Mapping
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 5: AI Visual Form Grounding")
    print("------------------------------------------------------------------------")
    print("Gemini will now take a screenshot of your app, locate the fields,")
    print("and propose the mapping between your Excel columns and the form.")
    print()
    print(f"--> Make sure {app_title!r} is open and visible on screen!")
    _prompt("Press Enter to start visual grounding")

    setup_rc = run_setup(str(config_file), assume_yes=False, env_path=env_path)
    if setup_rc != 0:
        print("\nSetup was cancelled or failed. You can run 'python main.py --agent' again.")
        return setup_rc

    # 6. First-Row Calibration
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 6: First-Row Calibration")
    print("------------------------------------------------------------------------")
    print("Calibration fills the first row, visually verifies the entered data")
    print("with Gemini, clicks Save, and confirms the post-save screen.")
    print("This earns 'trust' for the coordinate map before batch processing.")
    print()

    start_cal = _prompt_yes_no("Start calibration on the first pending row?", default_yes=True)
    if not start_cal:
        print("Calibration skipped. Run 'python main.py calibrate --config config/agent.interactive.json' later.")
        return 0

    cal_rc = run_calibrate(str(config_file), overlay=True, env_path=env_path)
    if cal_rc != 0:
        print("\nCalibration failed. Check data/screenshots/ and try again.")
        return cal_rc

    # 7. Final Automation Confirmation
    print()
    print("=" * 72)
    print("  READY FOR BATCH AUTOMATION")
    print("=" * 72)
    print(f"  Target Application : {app_title}")
    print(f"  Excel Input        : {excel_path}")
    print(f"  Field Mapping      : Validated and Trusted")
    print("  Safety Controls    : Alt+T (Stop) | Alt+P (Pause) | Alt+R (Resume)")
    print("=" * 72)
    print()

    # The user-requested question:
    start_automation = _prompt_yes_no("Do you want to start automation?", default_yes=True)
    if not start_automation:
        print("\nAutomation cancelled. Whenever you are ready, run:")
        print(f"  python main.py run --config {config_file}")
        return 0

    limit_input = _prompt("Process all rows, or limit count? [Enter for all, or type number]", default="")
    limit = int(limit_input) if limit_input.isdigit() and int(limit_input) > 0 else None

    print("\nStarting automation in 3 seconds... (Switch to your app if needed)")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1.0)

    return run_batch(str(config_file), limit=limit, overlay=True, env_path=env_path)
