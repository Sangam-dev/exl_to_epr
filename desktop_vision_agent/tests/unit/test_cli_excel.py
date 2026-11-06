"""CLI wiring tests for ``excel inspect`` (Segment 1)."""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from excel_agent.cli import main


def _write_book(path: Path, rows: list) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rows"
    sheet.append(["ID", "Name", "Amount", "Agent Status"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _write_config(path: Path, workbook_path: Path) -> Path:
    config = {
        "app": {"title_contains": "Test"},
        "excel": {
            "input_path": str(workbook_path),
            "output_path": str(path.parent / "output.xlsx"),
            "sheet": "Rows",
            "header_row": 1,
            "id_column": "ID",
        },
        "fields": [{"column": "Name", "field": "Name", "type": "text"}],
        "actions": {"save_button": "Save"},
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_excel_inspect_prints_pending_rows(tmp_path: Path, capsys) -> None:
    workbook_path = tmp_path / "book.xlsx"
    _write_book(
        workbook_path,
        [["A1", "Acme", 10, ""], ["A2", "Beta", 20, "DONE"]],
    )
    config_path = _write_config(tmp_path / "agent.json", workbook_path)

    exit_code = main(["excel", "inspect", "--config", str(config_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Rows: 2 | pending: 1 | skipped: 1" in output
    assert "Validation: OK" in output
    assert "A1" in output
    assert "skip" in output


def test_excel_inspect_reports_duplicate_ids(tmp_path: Path, capsys) -> None:
    workbook_path = tmp_path / "book.xlsx"
    _write_book(
        workbook_path,
        [["A1", "Acme", 10, ""], ["A1", "Beta", 20, ""]],
    )
    config_path = _write_config(tmp_path / "agent.json", workbook_path)

    exit_code = main(["excel", "inspect", "--config", str(config_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "duplicate" in output


def test_excel_inspect_reports_missing_config(tmp_path: Path, capsys) -> None:
    exit_code = main(
        ["excel", "inspect", "--config", str(tmp_path / "missing.json")]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "ERROR:" in output
