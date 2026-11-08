"""Integration test for fixture form interactions (Segment 9)."""

from __future__ import annotations

import subprocess
import time
from typing import Generator

import pytest

from excel_agent.desktop.uia import inspect_window
from excel_agent.desktop.window import find_window
from excel_agent.models.screen import WindowInfo


@pytest.fixture(scope="module")
def fixture_process() -> Generator[WindowInfo, None, None]:
    """Launch WinForms test form."""
    proc = subprocess.Popen(
        ["powershell", "-STA", "-ExecutionPolicy", "Bypass", "-File", "scripts/test_form.ps1"],
        cwd="desktop_vision_agent",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    window = None
    for _ in range(25):
        time.sleep(0.2)
        try:
            window = find_window("Excel Agent Test Form")
            break
        except Exception:
            pass

    if window is None:
        proc.terminate()
        pytest.skip("WinForms test fixture could not be opened in current environment.")

    try:
        yield window
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def test_fixture_form_controls_and_save_validation(fixture_process: WindowInfo) -> None:
    inspection = inspect_window(fixture_process)

    # Verify key controls are found
    fields_by_id = {c.automation_id: c for c in inspection.candidates if c.automation_id}
    assert "txtInvoiceNumber" in fields_by_id
    assert "txtCustomerName" in fields_by_id
    assert "txtAmount" in fields_by_id
    assert "btnSave" in fields_by_id
    assert "btnClear" in fields_by_id
    assert "lblStatus" in fields_by_id

    # Test Save validation when Invoice Number is empty
    save_cand = fields_by_id["btnSave"]
    inspection.invoke(save_cand.candidate_id)
    time.sleep(0.3)

    # Re-inspect to read updated status
    updated = inspect_window(fixture_process)
    status_cand = next(c for c in updated.candidates if c.automation_id == "lblStatus")
    status_text = updated.read_value(status_cand.candidate_id)
    assert "invoice number is required" in status_text.lower()


def test_fixture_form_clear_action(fixture_process: WindowInfo) -> None:
    inspection = inspect_window(fixture_process)
    fields_by_id = {c.automation_id: c for c in inspection.candidates if c.automation_id}

    # Click Clear
    clear_cand = fields_by_id["btnClear"]
    inspection.invoke(clear_cand.candidate_id)
    time.sleep(0.3)

    updated = inspect_window(fixture_process)
    status_cand = next(c for c in updated.candidates if c.automation_id == "lblStatus")
    status_text = updated.read_value(status_cand.candidate_id)
    assert "cleared" in status_text.lower()
