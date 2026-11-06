"""Workbook reading with pandas (Segment 1).

The reader keeps Excel row numbers intact so status writes always target the
same physical row. It never writes to the workbook.
"""

from __future__ import annotations

import math
import numbers
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from ..config import AgentConfig, FieldConfig, resolve_path


class ExcelReadError(RuntimeError):
    """Raised when the workbook cannot be read or is structurally invalid."""


@dataclass(frozen=True)
class ExcelRow:
    """One non-header row, with its original 1-based Excel row number."""

    row_number: int
    values: dict[str, Any]
    status: str = ""

    def is_blank(self) -> bool:
        return all(is_empty(value) for value in self.values.values())


@dataclass(frozen=True)
class Workbook:
    """A read-only view of the configured sheet."""

    path: Path
    sheet_name: str
    headers: tuple[str, ...]
    rows: tuple[ExcelRow, ...]


_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def is_empty(value: Any) -> bool:
    """True for None, NaN/NaT, and whitespace-only strings."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def format_value(value: Any, field_type: str) -> str:
    """Format one cell for typing into the target form.

    Text values keep leading zeros. Numbers are validated and returned without
    thousands separators. Empty cells return an empty string.
    """
    if is_empty(value):
        return ""
    if field_type == "text":
        return _format_text(value)
    if field_type == "number":
        return _format_number(value)
    raise ValueError(f"unsupported field type: {field_type!r}")


def _format_text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.hour or value.minute or value.second:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return str(number)
    return str(value).strip()


def _format_number(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"expected a finite number, got {value!r}")
        return str(int(number)) if number.is_integer() else str(number)
    if isinstance(value, str):
        text = value.strip()
        if not _NUMBER_RE.fullmatch(text):
            raise ValueError(f"not a number: {value!r}")
        return text
    raise ValueError(f"not a number: {value!r}")


def format_row(row: ExcelRow, fields: Iterable[FieldConfig]) -> dict[str, str]:
    """Return ``{form field: text to type}`` for one row."""
    return {
        field.field: format_value(row.values.get(field.column), field.type)
        for field in fields
    }


def _normalise_headers(values: Iterable[Any]) -> list[str]:
    headers: list[str] = []
    for position, value in enumerate(values, start=1):
        headers.append(f"Unnamed: {position}" if is_empty(value) else str(value).strip())
    return headers


def _duplicate_names(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def read_workbook(config: AgentConfig, path: str | Path | None = None) -> Workbook:
    """Read the configured sheet without modifying it."""
    excel = config.excel
    source = resolve_path(path if path is not None else excel.input_path)
    if not source.is_file():
        raise ExcelReadError(f"input workbook not found: {source}")

    try:
        raw = pd.read_excel(
            source,
            sheet_name=excel.sheet,
            header=None,
            dtype=object,
        )
    except Exception as exc:  # pandas raises several types for missing sheets
        raise ExcelReadError(
            f"cannot read {source} (sheet={excel.sheet!r}): {exc}"
        ) from exc

    header_index = excel.header_row - 1
    if header_index < 0 or header_index >= len(raw.index):
        raise ExcelReadError(
            f"header row {excel.header_row} is outside the sheet "
            f"({len(raw.index)} row(s) found)"
        )

    headers = _normalise_headers(raw.iloc[header_index].tolist())
    duplicates = _duplicate_names(headers)
    if duplicates:
        raise ExcelReadError(
            "duplicate column header(s): " + ", ".join(repr(name) for name in duplicates)
        )

    required = [excel.id_column, *[field.column for field in config.fields]]
    missing = [name for name in required if name not in headers]
    if missing:
        raise ExcelReadError(
            "configured column(s) not found in the header row: "
            + ", ".join(repr(name) for name in missing)
        )

    status_index = headers.index(excel.status_column) if excel.status_column in headers else None
    data = raw.iloc[header_index + 1 :]

    rows: list[ExcelRow] = []
    for index, series in data.iterrows():
        values = {name: series.iloc[position] for position, name in enumerate(headers)}
        status = ""
        if status_index is not None:
            status_value = series.iloc[status_index]
            status = "" if is_empty(status_value) else str(status_value).strip()
        rows.append(
            ExcelRow(
                row_number=int(index) + 1,
                values=values,
                status=status,
            )
        )

    return Workbook(
        path=source,
        sheet_name=str(excel.sheet),
        headers=tuple(headers),
        rows=tuple(rows),
    )


def pending_rows(workbook: Workbook, config: AgentConfig) -> list[ExcelRow]:
    """Rows that still need processing: not blank and not in ``skip_statuses``."""
    skip = {value.strip().upper() for value in config.excel.skip_statuses}
    return [
        row
        for row in workbook.rows
        if not row.is_blank() and row.status.strip().upper() not in skip
    ]


def validate_pending_rows(rows: Iterable[ExcelRow], config: AgentConfig) -> list[str]:
    """Return human-readable problems that would make processing unsafe."""
    issues: list[str] = []
    seen_ids: dict[str, int] = {}
    id_column = config.excel.id_column

    for row in rows:
        id_text = format_value(row.values.get(id_column), "text")
        if not id_text:
            issues.append(f"row {row.row_number}: {id_column!r} is empty")
        elif id_text in seen_ids:
            issues.append(
                f"row {row.row_number}: duplicate {id_column!r} "
                f"{id_text!r} (first seen on row {seen_ids[id_text]})"
            )
        else:
            seen_ids[id_text] = row.row_number

        for field in config.fields:
            try:
                format_value(row.values.get(field.column), field.type)
            except ValueError as exc:
                issues.append(f"row {row.row_number}: column {field.column!r}: {exc}")

    return issues
