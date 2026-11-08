# Segment 7 - Batch row runner

**Version:** `v0.8-batch`
**Status:** implemented
**Date:** 2026-11-08

## Goal

Process every pending row with the coordinate map that calibration proved,
without asking Gemini once per row.

## Deliverables

- `excel_agent/agent/row_runner.py`
  - `run_rows` iterates pending rows and applies `--limit`,
  - `run_row` resolves the live layout, fills, reads back, Saves, and verifies,
  - `classify_save` turns the pixel comparison into `saved`, `not_saved`, or
    `ambiguous`,
  - `layout_fingerprint` detects geometry changes between rows,
  - safe retries and `DONE` / `RECHECK` writes,
  - screenshots are kept only for rows that need attention.
- `excel_agent/agent/workspace.py`
  - `open_workspace` reuses the recorded working copy or creates one from the
    input,
  - `data/state/output.json` records the input path, output path, and timestamp
    with an atomic write.
- `python main.py run` with `--config`, `--mapping`, `--limit`, and
  `--no-overlay`.
- `RunResult` / `RowOutcome` summaries.
- 25 new tests.

## Demo

With the WinForms fixture open and the mapping already trusted:

```powershell
..\.venv\Scripts\python.exe main.py calibrate --config config/agent.test.json --no-overlay
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json --no-overlay
```

Observed:

```text
Workbook: ...\data\output\test_form_agent.xlsx
Pending rows: 3; processing: 3
Mapping: ...\data\state\mapping.json (trusted=True, 4 column(s))
Window: 0x808EA 'Excel Agent Test Form'
Output copy: ...\data\output\test_form_agent.xlsx (reused)
Gemini: gemini-3.6-flash (used only when the save is ambiguous)
  Alt+T stop | Alt+P pause | Alt+R resume

Row 4: attempt 1/3 (4 field(s))
  save check: saved (the filled fields changed)
Row 4: DONE
Row 5: attempt 1/3 (4 field(s))
  save check: saved (the filled fields changed)
Row 5: DONE
Row 6: attempt 1/3 (4 field(s))
  save check: saved (the filled fields changed)
Row 6: DONE

Processed 3 row(s): 3 DONE, 0 RECHECK
Row 4: DONE (attempt 1)
Row 5: DONE (attempt 1)
Row 6: DONE (attempt 1)
AI calls: 0
Output workbook: ...\data\output\test_form_agent.xlsx
```

The workbook showed:

```text
row 2 ['DONE', '2026-11-08 12:19:26', None]
row 3 ['DONE', None, None]                 # skipped
row 4 ['DONE', '2026-11-08 12:19:37', None]
row 5 ['DONE', '2026-11-08 12:19:41', None]
row 6 ['DONE', '2026-11-08 12:19:44', None]
```

The fixture listbox contained exactly four records: `INV-0001`, `INV-0003`,
`INV-0004`, and `INV-0005`. Row 2 came from calibration; rows 4-6 came from
the batch runner. Re-running `run` printed `No pending rows; nothing to run.`
and made no changes. Running `run` with a copy of the mapping that had
`trusted: false` refused to start with exit code 1.

## Acceptance

- **No Gemini call per field.** The three-row run made `0` AI calls. Gemini is
  reached only when `classify_save` returns `ambiguous`.
- **Layout changes are handled.** Every row re-inspects the live UIA tree, so
  moved windows and renamed controls are re-resolved without AI. The
  fingerprint logs when the geometry changed. A structural change that cannot
  be resolved aborts the run with an instruction to re-run `setup`.
- **Failed rows are retried N times, then marked `RECHECK`.** Covered by
  `test_run_row_retries_when_the_fields_are_unchanged`,
  `test_run_row_retries_a_read_back_mismatch`, and
  `test_run_row_does_not_retry_an_ambiguous_save_gemini_rejects`.
- **Excel is saved after each row.** `WorkbookWriter.write_status` writes the
  three status cells and saves atomically after every row.
- `pytest`: 232 passed (25 new tests).
- `python main.py --check`: passed.

## Decisions

- **A trusted map is mandatory.** `run` refuses to start while
  `mapping.trusted` is false. There is no override: calibration must pass
  first. The check is repeated inside `run_rows`, so the library cannot be
  used to bypass it either.
- **Gemini only for ambiguity.** `classify_save` trusts the pixel comparison
  when at least one non-empty mapped field was compared. No comparison
  (all values empty or the window size changed) is ambiguous and gets one
  Gemini check.
- **Retry only a save that definitely did not happen.** A read-back mismatch or
  an unchanged form retries. An ambiguous form that Gemini will not approve is
  marked `RECHECK` immediately, because the record may already exist.
- **A stop after Save is uncertain.** The row becomes `RECHECK`; a stop before
  Save leaves the row pending.
- **One working copy per input.** `data/state/output.json` records the copy so
  `calibrate` and `run` share progress. Without it, `run` would re-read the
  input, see the calibration row as pending, and save it twice. The record is
  ignored when the input path differs or the copy is gone.
- **Per-row UIA re-resolution instead of per-row grounding.** Grounding means a
  Gemini call plus a new approval, which cannot happen unattended mid-run.
  Re-reading UIA is deterministic, cheap, and handles the common case (the
  window moved or a control was re-created). A genuinely new layout still
  requires `setup`.
- **Screenshots on failure only.** A clean row keeps its captures in memory for
  the diff and writes nothing to disk. `RECHECK` and ambiguous rows keep their
  pre/post images under `data/screenshots/`.

## Known limitations

- The working copy snapshots the input at the first `calibrate` or `run`.
  Rows added to the input afterwards are not seen until the working-copy record
  is deleted.
- `--limit` counts pending rows, not physical Excel rows.
- There is still no run-state file or cross-process resume. Segment 8 adds
  `run_state.json` and `resume`.
- A layout that changes structurally (a field disappears) stops the run and
  requires a fresh `setup`; there is no unattended re-grounding.
