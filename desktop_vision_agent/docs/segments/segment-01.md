# Segment 1 - Excel read/write & row model

**Version:** `v0.2-excel`
**Status:** implemented
**Date:** 2026-11-07

## Goal

Read the source workbook safely, work on a copy, and track row status without
touching the original file.

## Deliverables

- `excel_agent/excel/reader.py`
  - pandas read with `header=None` so Excel row numbers stay exact,
  - header normalisation, duplicate-header detection, required-column checks,
  - pending-row filtering (blank rows and `skip_statuses`),
  - text/number formatting (leading zeros preserved, numbers validated),
  - pending-row validation (empty and duplicate IDs, bad numbers).
- `excel_agent/excel/writer.py`
  - `prepare_output` copies the input and never overwrites an existing output,
  - `WorkbookWriter` adds/reuses `Agent Status`, `Agent Timestamp`, `Agent Error`,
  - one atomic save per `write_status` call,
  - clear error when the workbook is open in Excel.
- `scripts/make_sample_excel.py` creates `data/input/invoices.xlsx` with
  formatting, dates, an amount format, a formula column, and one `DONE` row.
- `python main.py excel inspect` prints headers, rows, pending/skip state, and
  validation issues.
- Unit tests for reader, writer, and CLI wiring.

## Demo

```powershell
cd desktop_vision_agent
..\.venv\Scripts\python.exe scripts\make_sample_excel.py
..\.venv\Scripts\python.exe main.py excel inspect --config config/agent.example.json
```

Observed:

```text
Rows: 5 | pending: 4 | skipped: 1
Validation: OK
```

The `DONE` row is skipped. The four pending rows are shown with their mapped
values.

Writer check (also covered by tests):

```text
output: data/output/invoices_agent.xlsx
headers: [..., 'Agent Status', 'Agent Timestamp', 'Agent Error']
row 2: DONE
row 4: RECHECK | demo: amount missing
formula I2: =D2*1.2
bold header: True
amount format: #,##0.00
```

## Acceptance

- Original workbook untouched: `prepare_output` only copies; tests assert the
  source has no status columns after writing.
- Output copy contains the three status columns.
- A row can be marked `DONE` or `RECHECK`, with a timestamp and error text.
- Formatting, number formats, and formulas survive the status write.
- Existing status columns are reused, not duplicated.
- Mapped columns and the ID column cannot collide with the three status columns.
- `WorkbookWriter` refuses to target the original input workbook.
- `pytest`: 57 passed.
- `python main.py --check`: passed.

## Decisions

- Reader functions take the top-level `AgentConfig` because the field mapping
  lives there; `ExcelConfig` alone is not enough.
- `header_row` in JSON is 1-based (Excel row 1 is the first row).
- Column matching is exact after trimming surrounding whitespace. This is
  deliberate: silently fuzzy-matching headers could map the wrong column.
- The configured output path is used when free; if it already exists, a
  timestamped sibling is created. Previous runs are never overwritten.
- Status writes use openpyxl on the copy, not pandas, to preserve formatting
  and formulas. Each row is saved atomically via a temporary file + replace.
- Empty error text is written as an empty cell (`None`), not `""`.

## Known limitations

- `openpyxl` cannot reliably save while the output workbook is open in Excel;
  the writer raises a clear error and asks the user to close it.
- A mapped column that contains a formula may read as empty because pandas uses
  cached values. Keep formula columns unmapped, or ensure the workbook was
  recalculated and saved by Excel.
- Macro-enabled workbooks (`.xlsm`) and charts/images are out of scope.
- Column names are case-sensitive.
- Leading/trailing blank rows are not data; blank rows in the middle are
  detected and skipped.
