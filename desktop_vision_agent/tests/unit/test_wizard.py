"""Unit tests for the interactive agent wizard (python main.py --agent)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from excel_agent.agent.wizard import (
    _clean_path,
    _generate_config,
    _prompt,
    _prompt_yes_no,
    run_wizard,
)


def test_clean_path_removes_quotes_and_whitespace() -> None:
    assert _clean_path('  "C:/data/invoices.xlsx"  ') == "C:/data/invoices.xlsx"
    assert _clean_path("  'C:/data/invoices.xlsx'  ") == "C:/data/invoices.xlsx"
    assert _clean_path("C:/data/invoices.xlsx") == "C:/data/invoices.xlsx"


def test_prompt_uses_default_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt("Enter value", default="default_val") == "default_val"


def test_prompt_returns_user_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "my_value")
    assert _prompt("Enter value", default="default_val") == "my_value"


def test_prompt_yes_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _prompt_yes_no("Proceed?") is True

    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert _prompt_yes_no("Proceed?") is False

    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes_no("Proceed?", default_yes=True) is True
    assert _prompt_yes_no("Proceed?", default_yes=False) is False


def test_generate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)

    config_path = _generate_config(
        app_title="Test App",
        process_name="app.exe",
        excel_path="C:/data/test.xlsx",
        sheet_name="Invoices",
        id_column="InvoiceID",
        headers=["InvoiceID", "Customer", "Amount"],
        save_button="Submit",
    )

    assert config_path.is_file()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["app"]["title_contains"] == "Test App"
    assert data["app"]["process_name"] == "app.exe"
    assert data["excel"]["sheet"] == "Invoices"
    assert data["excel"]["id_column"] == "InvoiceID"
    assert data["actions"]["save_button"] == "Submit"


def test_run_wizard_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_window",
        lambda: ("Test App", "app.exe"),
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_excel",
        lambda: ("C:/data/test.xlsx", "Sheet1", "ID", ["ID", "Name"]),
    )
    monkeypatch.setattr("excel_agent.agent.wizard._prompt", lambda prompt, default="": default or "Save")
    monkeypatch.setattr("excel_agent.agent.wizard._prompt_yes_no", lambda q, default_yes=True: True)

    mock_setup = MagicMock(return_value=0)
    mock_calibrate = MagicMock(return_value=0)
    mock_batch = MagicMock(return_value=0)

    monkeypatch.setattr("excel_agent.cli.run_setup", mock_setup)
    monkeypatch.setattr("excel_agent.cli.run_calibrate", mock_calibrate)
    monkeypatch.setattr("excel_agent.cli.run_batch", mock_batch)

    # Fast forward time.sleep in wizard
    monkeypatch.setattr("time.sleep", lambda _: None)

    exit_code = run_wizard()
    assert exit_code == 0
    assert mock_setup.called
    assert mock_calibrate.called
    assert mock_batch.called
