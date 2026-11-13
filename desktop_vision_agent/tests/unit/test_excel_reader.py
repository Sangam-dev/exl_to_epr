"""Unit tests for workbook reading (Segment 1)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from excel_agent.config import AgentConfig
from excel_agent.excel.reader import (
    ExcelReadError,
    format_row,
    format_value,
    pending_rows,
    read_workbook,
    validate_pending_rows,
)


def _make_workbook(path: Path, headers: list, rows: list, sheet: str = "Rows") -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


def _config(
    path: Path,
    tmp_path: Path,
    fields: list[dict] | None = None,
    **excel_overrides,
) -> AgentConfig:
    excel = {
        "input_path": str(path),
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
            "fields": fields
            if fields is not None
            else [{"column": "Name", "field": "Name", "type": "text"}],
            "actions": {"save_button": "Save"},
        }
    )


def test_read_workbook_keeps_excel_row_numbers(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name", "Amount", "Agent Status"],
        [
            [1, "Acme", 10, ""],
            [None, None, None, None],
            [2, "Beta", 20, "DONE"],
            [3, "Cedar", 30, "RECHECK"],
        ],
    )

    workbook = read_workbook(_config(source, tmp_path))

    assert workbook.headers == ("ID", "Name", "Amount", "Agent Status")
    assert [row.row_number for row in workbook.rows] == [2, 3, 4, 5]
    assert [row.status for row in workbook.rows] == ["", "", "DONE", "RECHECK"]
    assert workbook.rows[1].is_blank()


def test_pending_rows_skips_done_recheck_and_blank(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name", "Agent Status"],
        [
            [1, "Acme", ""],
            [None, None, None],
            [2, "Beta", "DONE"],
            [3, "Cedar", "RECHECK"],
        ],
    )
    config = _config(source, tmp_path)
    workbook = read_workbook(config)

    assert [row.row_number for row in pending_rows(workbook, config)] == [2]


def test_pending_rows_can_opt_back_into_recheck(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name", "Agent Status"],
        [
            [1, "Acme", ""],
            [2, "Beta", "DONE"],
            [3, "Cedar", "RECHECK"],
        ],
    )
    config = _config(source, tmp_path, skip_statuses=["DONE"])
    workbook = read_workbook(config)

    assert [row.row_number for row in pending_rows(workbook, config)] == [2, 4]


def test_header_row_can_be_below_the_first_line(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rows"
    sheet.append(["Invoice export"])
    sheet.append(["ID", "Name"])
    sheet.append([1, "Acme"])
    workbook.save(path)

    result = read_workbook(_config(path, tmp_path, header_row=2))

    assert result.headers == ("ID", "Name")
    assert result.rows[0].row_number == 3


def test_missing_mapped_column_raises(tmp_path: Path) -> None:
    source = _make_workbook(tmp_path / "book.xlsx", ["ID", "Name"], [[1, "Acme"]])
    config = _config(
        source,
        tmp_path,
        fields=[{"column": "Missing", "field": "Missing", "type": "text"}],
    )

    with pytest.raises(ExcelReadError, match="not found"):
        read_workbook(config)


def test_duplicate_headers_raise(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name", "Name"],
        [[1, "Acme", "Other"]],
    )

    with pytest.raises(ExcelReadError, match="duplicate column header"):
        read_workbook(_config(source, tmp_path))


def test_missing_sheet_raises(tmp_path: Path) -> None:
    source = _make_workbook(tmp_path / "book.xlsx", ["ID", "Name"], [[1, "Acme"]])

    with pytest.raises(ExcelReadError, match="cannot read"):
        read_workbook(_config(source, tmp_path, sheet="NoSuchSheet"))


def test_format_row_maps_columns_to_fields(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name", "Amount"],
        [[1, "Acme", 10.5]],
    )
    config = _config(
        source,
        tmp_path,
        fields=[
            {"column": "Name", "field": "Customer Name", "type": "text"},
            {"column": "Amount", "field": "Amount", "type": "number"},
        ],
    )
    row = pending_rows(read_workbook(config), config)[0]

    assert format_row(row, config.fields) == {"Customer Name": "Acme", "Amount": "10.5"}


@pytest.mark.parametrize(
    ("value", "field_type", "expected"),
    [
        (123.0, "text", "123"),
        (123.5, "text", "123.5"),
        ("00123", "text", "00123"),
        (date(2026, 1, 5), "text", "2026-01-05"),
        (datetime(2026, 1, 5, 14, 30), "text", "2026-01-05 14:30:00"),
        (True, "text", "True"),
        (None, "text", ""),
        (float("nan"), "text", ""),
        (12, "number", "12"),
        (12.0, "number", "12"),
        (12.5, "number", "12.5"),
        ("00123", "number", "00123"),
        ("-4.25", "number", "-4.25"),
    ],
)
def test_format_value(value, field_type: str, expected: str) -> None:
    assert format_value(value, field_type) == expected


@pytest.mark.parametrize("value", ["abc", "1,234"])
def test_format_number_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="not a number"):
        format_value(value, "number")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_format_number_allows_empty_values(value) -> None:
    assert format_value(value, "number") == ""


def test_validate_pending_rows_reports_empty_and_duplicate_ids(tmp_path: Path) -> None:
    source = _make_workbook(
        tmp_path / "book.xlsx",
        ["ID", "Name"],
        [["A1", "Acme"], ["A1", "Beta"], [None, "Cedar"]],
    )
    config = _config(source, tmp_path)
    rows = pending_rows(read_workbook(config), config)

    issues = validate_pending_rows(rows, config)

    assert any("duplicate" in issue for issue in issues)
    assert any("is empty" in issue for issue in issues)
