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
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..config import AgentConfig, project_root, resolve_path
from ..desktop.window import WindowError, find_window, focus_window, list_windows, pin_window
from ..excel.reader import pending_rows, read_workbook
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


def _generate_config(
    app_title: str,
    process_name: str | None,
    excel_path: str,
    sheet_name: str,
    id_column: str,
    headers: list[str],
    save_button: str,
) -> Path:
    """Generate and write a customized configuration file."""
    config_dir = project_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "agent.interactive.json"

    # Derive output workbook name
    p = Path(excel_path)
    output_path = f"data/output/{p.stem}_agent{p.suffix}"

    field_configs = [
        {"column": col, "field": col, "type": "text"}
        for col in headers
        if not col.lower().startswith("agent ")
    ]

    config_data = {
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
        "safety": {
            "stop_hotkey": "alt+t",
            "pause_hotkey": "alt+p",
            "resume_hotkey": "alt+r",
            "block_external_input": True,
            "max_attempts": 3,
            "action_delay_seconds": 0.15,
            "stop_on_failure": False,
        },
    }

    config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
    return config_path


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

    # 3. Action settings
    print()
    print("------------------------------------------------------------------------")
    print("  STEP 3: Form Actions & Settings")
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
    print("  STEP 4: AI Visual Form Grounding")
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
    print("  STEP 5: First-Row Calibration")
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
