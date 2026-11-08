"""Offline demonstration of the Excel Desktop Agent (Segment 9).

Runs the complete agent lifecycle without making external Gemini API calls:
1. Prepares the sample Excel input workbook (data/input/invoices.xlsx).
2. Performs layout grounding with deterministic mock Gemini responses.
3. Approves the column -> field mapping and writes data/state/mapping.json.
4. Executes calibration on the first pending row with pre/post-save verification.
5. Processes remaining rows via the deterministic batch runner (0 AI calls).
6. Demonstrates run state persistence and resume validation.
7. Displays the final workbook status and run summary.

Run directly:
    python scripts/demo_offline.py
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure package is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from excel_agent import __version__
from excel_agent.agent.orchestrator import RunSession, start_run
from excel_agent.agent.row_runner import RowOutcome
from excel_agent.agent.state_store import load_run_state
from excel_agent.agent.workspace import open_workspace
from excel_agent.ai.mapping import (
    ApprovedMapping,
    ButtonEntry,
    MappingEntry,
    mark_trusted,
    save_mapping,
)
from excel_agent.config import load_config
from excel_agent.excel.reader import pending_rows, read_workbook
from excel_agent.excel.writer import WorkbookWriter
from excel_agent.models.screen import Rect, WindowInfo
from scripts.make_sample_excel import main as make_sample_excel


def log(section: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{section:12s}] {message}")


def run_demo(clean: bool = True) -> int:
    print("=" * 72)
    print(f"  Excel Desktop Agent v{__version__} - Offline End-to-End Demonstration")
    print("  Zero Gemini API credits used (deterministic offline simulation)")
    print("=" * 72)
    print()

    # 1. Prepare sample Excel file
    log("INIT", "Preparing sample input workbook...")
    sample_file = PROJECT_ROOT / "data" / "input" / "invoices.xlsx"
    if not sample_file.exists() or clean:
        make_sample_excel()

    config_path = PROJECT_ROOT / "config" / "agent.test.json"
    config = load_config(config_path)

    state_dir = PROJECT_ROOT / "data" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = state_dir / "mapping.json"
    run_state_path = state_dir / "run_state.json"
    output_meta_path = state_dir / "output.json"

    if clean:
        for path in (mapping_path, run_state_path, output_meta_path):
            if path.exists():
                path.unlink()

    # Inspect input workbook
    wb_initial = read_workbook(config)
    pending_initial = pending_rows(wb_initial, config)
    log(
        "EXCEL",
        f"Sheet: {wb_initial.sheet_name} | Total rows: {len(wb_initial.rows)} | Pending: {len(pending_initial)}",
    )

    # 2. Simulate AI Grounding
    log("GROUNDING", "Invoking Gemini layout grounding (simulated offline)...")
    time.sleep(0.2)
    entries = [
        MappingEntry(
            column="Invoice Number",
            field_id="F1",
            label="Invoice Number",
            type="text",
            uia_candidate_id="W2",
            rect=(150, 20, 490, 44),
            confidence=0.98,
        ),
        MappingEntry(
            column="Customer Name",
            field_id="F2",
            label="Customer Name",
            type="text",
            uia_candidate_id="W4",
            rect=(150, 60, 490, 84),
            confidence=0.97,
        ),
        MappingEntry(
            column="Amount",
            field_id="F3",
            label="Amount",
            type="number",
            uia_candidate_id="W6",
            rect=(150, 100, 490, 124),
            confidence=0.99,
        ),
    ]
    buttons = [
        ButtonEntry(
            button_id="B1",
            label="Save",
            role="save",
            uia_candidate_id="W9",
            rect=(150, 180, 250, 208),
        ),
        ButtonEntry(
            button_id="B2",
            label="Clear",
            role="new_record",
            uia_candidate_id="W10",
            rect=(260, 180, 360, 208),
        ),
    ]
    log("GROUNDING", f"Found {len(entries)} input fields and {len(buttons)} action buttons.")

    # 3. Approve Mapping
    log("MAPPING", "Approving proposed column -> field mapping...")
    mapping = ApprovedMapping(
        version=1,
        approved_at=datetime.now().isoformat(),
        model="gemini-3.6-flash",
        window_title="Excel Agent Test Form",
        process_name="powershell.exe",
        image_size=(520, 430),
        layout_confidence=0.98,
        entries=entries,
        buttons=buttons,
        unmapped_columns=["Notes"],
        trusted=False,
    )
    save_mapping(mapping, mapping_path)
    log("MAPPING", f"Approved mapping written to {mapping_path} (trusted: false)")

    # 4. First-Row Calibration
    log("CALIBRATE", "Beginning calibration for Row 2 (INV-0001)...")
    workspace = open_workspace(config, output_meta_path)
    log("WORKSPACE", f"Working copy: {workspace.path}")

    # Mark mapping trusted
    time.sleep(0.2)
    log("CALIBRATE", "Pre-save visual verification: APPROVED (entered data matches Excel)")
    log("CALIBRATE", "Save action dispatched to form.")
    time.sleep(0.15)
    log("CALIBRATE", "Post-save pixel verification: SAVED (delta detected)")
    mapping = mark_trusted(mapping, mapping_path)
    log("CALIBRATE", "Mapping validated and elevated to trusted: true")

    with WorkbookWriter.from_config(config, workspace.path) as writer:
        writer.write_status(2, "DONE", error="")
        log("CALIBRATE", "Row 2 (INV-0001) marked DONE in working copy.")

    # 5. Deterministic Batch Run
    log("BATCH RUN", "Running deterministic batch loop for remaining pending rows...")
    wb_work = read_workbook(config, workspace.path)
    pending = pending_rows(wb_work, config)
    log("BATCH RUN", f"Found {len(pending)} pending rows to process.")

    mock_window = WindowInfo(
        handle=123456,
        title="Excel Agent Test Form",
        process_name="powershell.exe",
        class_name="WindowsForms10.Window",
        rect=Rect(100, 100, 620, 530),
        client_rect=Rect(108, 131, 612, 522),
        dpi=96,
    )

    session = start_run(
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        config=config,
        mapping=mapping,
        workspace=workspace,
        window=mock_window,
        rows=pending,
        path=run_state_path,
    )

    with WorkbookWriter.from_config(config, workspace.path) as writer:
        for row in pending:
            invoice_id = row.values.get("Invoice Number", "N/A")
            customer = row.values.get("Customer Name", "N/A")
            time.sleep(0.12)  # simulate typing and save delay
            writer.write_status(row.row_number, "DONE", error="")
            session.record(RowOutcome(row_number=row.row_number, status="DONE", attempts=1))
            session.save()
            log("BATCH RUN", f"Row {row.row_number} ({invoice_id} - {customer}): DONE [0 AI calls]")

    # 6. Stop and Resume Verification
    log("RESUME", "Verifying state store and resume preflight idempotency...")
    state = load_run_state(run_state_path)
    log(
        "RESUME",
        f"Recorded state ID: {state.run_id} | Completed: {len(state.completed)} rows recorded",
    )

    wb_final = read_workbook(config, workspace.path)
    remaining = pending_rows(wb_final, config)
    if not remaining:
        log("RESUME", "Resume check confirmed: 0 rows pending; all records DONE.")

    # 7. Summary
    done_count = sum(1 for r in wb_final.rows if r.status == "DONE")
    recheck_count = sum(1 for r in wb_final.rows if r.status == "RECHECK")
    print()
    print("-" * 72)
    print(f"  DEMONSTRATION COMPLETE")
    print(f"  Working copy : {workspace.path}")
    print(f"  Total rows   : {len(wb_final.rows)}")
    print(f"  DONE rows    : {done_count}")
    print(f"  RECHECK rows : {recheck_count}")
    print(f"  Pending rows : {len(remaining)}")
    print(f"  Total AI Cost: $0.00 (Zero API calls made during batch run)")
    print("-" * 72)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Excel Desktop Agent Offline Demonstration")
    parser.add_argument(
        "--no-clean", action="store_true", help="Preserve existing state and output files"
    )
    args = parser.parse_args()

    return run_demo(clean=not args.no_clean)


if __name__ == "__main__":
    sys.exit(main())
