"""Integration tests for UI Automation inspection (Segment 9)."""

from __future__ import annotations

import subprocess
import time
from typing import Generator

import pytest

from excel_agent.desktop.uia import UIAError, inspect_window
from excel_agent.desktop.window import find_window
from excel_agent.models.screen import Rect, WindowInfo


@pytest.fixture(scope="module")
def winforms_fixture() -> Generator[WindowInfo, None, None]:
    """Launch the WinForms test fixture for UIA testing."""
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


def test_uia_inspect_invalid_window_raises() -> None:
    invalid_win = WindowInfo(
        handle=99999999,
        title="Invalid",
        process_name="invalid.exe",
        class_name="None",
        rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100),
        dpi=96,
    )
    with pytest.raises(UIAError):
        inspect_window(invalid_win)


def test_uia_inspect_fixture_candidates(winforms_fixture: WindowInfo) -> None:
    inspection = inspect_window(winforms_fixture)
    assert len(inspection.candidates) >= 5

    # Check candidate retrieval
    cand1 = inspection.candidate("W1")
    assert cand1.candidate_id == "W1"

    # Verify that unknown candidate raises UIAError
    with pytest.raises(UIAError, match="unknown UIA candidate"):
        inspection.candidate("W999")

    # Find txtInvoiceNumber edit control
    invoice_cand = next(
        (c for c in inspection.candidates if c.automation_id == "txtInvoiceNumber"), None
    )
    assert invoice_cand is not None
    assert invoice_cand.control_type == "Edit"

    # Focus and read value
    inspection.focus(invoice_cand.candidate_id)
    val = inspection.read_value(invoice_cand.candidate_id)
    assert isinstance(val, str)
