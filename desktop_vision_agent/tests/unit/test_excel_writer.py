"""Unit tests for workbook copying and status writing (Segment 1)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from excel_agent.config import AgentConfig
from excel_agent.excel.writer import ExcelWriteError, WorkbookWriter, prepare_output


def _make_source(path: Path, headers: list | None = None, rows: list | None = None) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rows"
    sheet.append(headers or ["ID", "Name", "Amount", "Total"])
    for row in rows or [["A1", "Acme", 10, "=C2*2"], ["A2", "Beta", 20, "=C3*2"]]:
        sheet.append(row)
    sheet["A1"].font = Font(bold=True)
    sheet["C2"].number_format = "#,##0.00"
    workbook.save(path)
    return path


def _config(source: Path, tmp_path: Path, **excel_overrides) -> AgentConfig:
    excel = {
        "input_path": str(source),
        "output_path": str(tmp_path / "output.xlsx"),
        "sheet": "Rows",
        "header_row": 1,
        "id_column": "ID",
    }
    excel.update(excel_overrides)
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Test"},
            "excel": excel,
            "fields": [
                {"column": "Name", "field": "Name", "type": "text"},
                {"column": "Amount", "field": "Amount", "type": "number"},
            ],
            "actions": {"save_button": "Save"},
        }
    )


def test_prepare_output_copies_without_touching_source(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)

    target = prepare_output(config.excel)

    assert target == tmp_path / "output.xlsx"
    assert target.is_file()

    source_book = load_workbook(source)
    source_sheet = source_book["Rows"]
    assert [cell.value for cell in source_sheet[1]] == ["ID", "Name", "Amount", "Total"]

    target_book = load_workbook(target)
    target_sheet = target_book["Rows"]
    assert [cell.value for cell in target_sheet[1]] == ["ID", "Name", "Amount", "Total"]
    assert target_sheet["B2"].value == "Acme"


def test_prepare_output_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)
    stamp = datetime(2026, 1, 1, 12, 0, 0)

    first = prepare_output(config.excel, timestamp=stamp)
    second = prepare_output(config.excel, timestamp=stamp)

    assert first.name == "output.xlsx"
    assert second.name == "output_20260101_120000.xlsx"
    assert first.is_file() and second.is_file()


def test_prepare_output_rejects_same_input_and_output(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path, output_path=str(source))

    with pytest.raises(ExcelWriteError, match="must differ"):
        prepare_output(config.excel)


def test_prepare_output_rejects_missing_input(tmp_path: Path) -> None:
    config = _config(tmp_path / "missing.xlsx", tmp_path)

    with pytest.raises(ExcelWriteError, match="not found"):
        prepare_output(config.excel)


def test_writer_adds_columns_and_writes_status(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)
    target = prepare_output(config.excel, timestamp=datetime(2026, 1, 1, 12, 0, 0))

    with WorkbookWriter.from_config(config, path=target) as writer:
        writer.write_status(
            2, "DONE", timestamp=datetime(2026, 1, 1, 12, 0, 0)
        )
        writer.write_status(
            3, "RECHECK", error="amount invalid", timestamp=datetime(2026, 1, 1, 12, 0, 1)
        )

    workbook = load_workbook(target)
    sheet = workbook["Rows"]

    assert [cell.value for cell in sheet[1]] == [
        "ID",
        "Name",
        "Amount",
        "Total",
        "Agent Status",
        "Agent Timestamp",
        "Agent Error",
    ]
    assert sheet.cell(2, 5).value == "DONE"
    assert sheet.cell(2, 6).value == "2026-01-01 12:00:00"
    assert sheet.cell(2, 7).value is None
    assert sheet.cell(3, 5).value == "RECHECK"
    assert sheet.cell(3, 7).value == "amount invalid"


def test_writer_preserves_formatting_and_formulas(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)
    target = prepare_output(config.excel)

    with WorkbookWriter.from_config(config, path=target) as writer:
        writer.write_status(2, "DONE")

    sheet = load_workbook(target)["Rows"]

    assert sheet["A1"].font.bold is True
    assert sheet["C2"].number_format == "#,##0.00"
    assert sheet["D2"].value == "=C2*2"


def test_writer_reuses_existing_status_column(tmp_path: Path) -> None:
    source = _make_source(
        tmp_path / "input.xlsx",
        headers=["ID", "Name", "Agent Status"],
        rows=[["A1", "Acme", ""]],
    )
    config = _config(source, tmp_path)
    target = prepare_output(config.excel)

    with WorkbookWriter.from_config(config, path=target) as writer:
        writer.write_status(2, "DONE")

    sheet = load_workbook(target)["Rows"]

    assert [cell.value for cell in sheet[1]] == [
        "ID",
        "Name",
        "Agent Status",
        "Agent Timestamp",
        "Agent Error",
    ]
    assert sheet.cell(2, 3).value == "DONE"


def test_writer_rejects_unknown_status(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)
    target = prepare_output(config.excel)

    with WorkbookWriter.from_config(config, path=target) as writer:
        with pytest.raises(ExcelWriteError, match="invalid status"):
            writer.write_status(2, "MAYBE")


def test_writer_rejects_header_row_as_target(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)
    target = prepare_output(config.excel)

    with WorkbookWriter.from_config(config, path=target) as writer:
        with pytest.raises(ExcelWriteError, match="not below the header"):
            writer.write_status(1, "DONE")


def test_writer_rejects_the_original_workbook_as_target(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path)

    with pytest.raises(ExcelWriteError, match="must differ"):
        WorkbookWriter.from_config(config, path=source)
