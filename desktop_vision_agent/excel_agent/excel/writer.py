"""Status writing with openpyxl (Segment 1).

The original workbook is never modified. ``prepare_output`` copies it, and
``WorkbookWriter`` writes only the three status columns and saves atomically
after every row.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..config import AgentConfig, ExcelConfig, resolve_path


class ExcelWriteError(RuntimeError):
    """Raised when the output copy cannot be created or saved."""


def prepare_output(config: ExcelConfig, timestamp: datetime | None = None) -> Path:
    """Copy the input workbook to a fresh output path and return it.

    The configured output path is used when it does not exist. If it already
    exists, a timestamped sibling is created instead so previous runs are never
    overwritten.
    """
    source = resolve_path(config.input_path)
    target = resolve_path(config.output_path)

    if not source.is_file():
        raise ExcelWriteError(f"input workbook not found: {source}")
    if source.resolve() == target.resolve():
        raise ExcelWriteError("output_path must differ from input_path")

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
        suffix = target.suffix or ".xlsx"
        candidate = target.with_name(f"{target.stem}_{stamp}{suffix}")
        counter = 1
        while candidate.exists():
            candidate = target.with_name(f"{target.stem}_{stamp}_{counter}{suffix}")
            counter += 1
        target = candidate

    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise ExcelWriteError(f"cannot copy {source} to {target}: {exc}") from exc
    return target


def _select_sheet(workbook, sheet: str | int) -> Worksheet:
    if isinstance(sheet, int):
        if sheet < 0 or sheet >= len(workbook.worksheets):
            raise ExcelWriteError(f"sheet index {sheet} is out of range")
        return workbook.worksheets[sheet]
    if sheet not in workbook.sheetnames:
        raise ExcelWriteError(f"sheet {sheet!r} not found in the workbook")
    return workbook[sheet]


class WorkbookWriter:
    """Write ``DONE`` / ``RECHECK`` status cells to the output copy."""

    def __init__(
        self,
        path: str | Path,
        sheet: str | int,
        header_row: int,
        status_column: str,
        timestamp_column: str,
        error_column: str,
        allowed_statuses: set[str],
    ) -> None:
        self.path = Path(path)
        self._allowed_statuses = set(allowed_statuses)
        self._closed = False

        try:
            self._workbook = load_workbook(self.path)
        except Exception as exc:
            raise ExcelWriteError(f"cannot open output workbook {self.path}: {exc}") from exc

        self._sheet = _select_sheet(self._workbook, sheet)
        self._header_row = header_row
        self._columns = self._ensure_columns(
            {
                "status": status_column,
                "timestamp": timestamp_column,
                "error": error_column,
            }
        )

    @classmethod
    def from_config(cls, config: AgentConfig, path: str | Path | None = None) -> "WorkbookWriter":
        if path is not None:
            target = Path(path)
            source = resolve_path(config.excel.input_path)
            if target.resolve() == source.resolve():
                raise ExcelWriteError("output path must differ from input_path")
        else:
            target = prepare_output(config.excel)
        return cls(
            path=target,
            sheet=config.excel.sheet,
            header_row=config.excel.header_row,
            status_column=config.excel.status_column,
            timestamp_column=config.excel.timestamp_column,
            error_column=config.excel.error_column,
            allowed_statuses={config.excel.done_value, config.excel.recheck_value},
        )

    def _ensure_columns(self, names: dict[str, str]) -> dict[str, int]:
        headers: dict[str, int] = {}
        max_column = self._sheet.max_column or 0

        for column in range(1, max_column + 1):
            value = self._sheet.cell(self._header_row, column).value
            if value is None:
                continue
            name = str(value).strip()
            if name in headers:
                raise ExcelWriteError(
                    f"duplicate header {name!r} in row {self._header_row}"
                )
            headers[name] = column

        result: dict[str, int] = {}
        next_column = max_column + 1
        for key, name in names.items():
            if name in headers:
                result[key] = headers[name]
                continue
            self._sheet.cell(self._header_row, next_column, name)
            headers[name] = next_column
            result[key] = next_column
            next_column += 1
        return result

    def write_status(
        self,
        row_number: int,
        status: str,
        error: str = "",
        timestamp: datetime | None = None,
    ) -> None:
        """Write one row's status, timestamp, and error, then save."""
        if self._closed:
            raise ExcelWriteError("writer is closed")
        if status not in self._allowed_statuses:
            allowed = ", ".join(sorted(self._allowed_statuses))
            raise ExcelWriteError(f"invalid status {status!r}; allowed: {allowed}")
        if row_number <= self._header_row:
            raise ExcelWriteError(
                f"row {row_number} is not below the header row {self._header_row}"
            )

        stamp = (timestamp or datetime.now()).replace(microsecond=0).isoformat(sep=" ")
        self._sheet.cell(row_number, self._columns["status"], status)
        self._sheet.cell(row_number, self._columns["timestamp"], stamp)
        self._sheet.cell(row_number, self._columns["error"], error or None)
        self._save()

    def _save(self) -> None:
        # Write to a temporary file first, then replace the target. A crash
        # mid-write cannot corrupt the previous good copy.
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.stem}_",
                suffix=".xlsx",
            )
            os.close(fd)
            self._workbook.save(temp_name)
            os.replace(temp_name, self.path)
        except PermissionError as exc:
            raise ExcelWriteError(
                f"cannot save {self.path}; close the workbook in Excel and retry"
            ) from exc
        except OSError as exc:
            raise ExcelWriteError(f"cannot save {self.path}: {exc}") from exc
        finally:
            if temp_name is not None:
                try:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                except OSError:
                    pass

    def close(self) -> None:
        if not self._closed:
            self._workbook.close()
            self._closed = True

    def __enter__(self) -> "WorkbookWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
