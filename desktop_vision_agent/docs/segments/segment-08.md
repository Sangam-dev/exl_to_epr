# Segment 8 - Stop/resume & state

**Version:** `v0.9-resume`
**Status:** implemented
**Date:** 2026-11-08

## Goal

Stop safely mid-batch and continue later without duplicates. A row that was
saved carries `DONE` in the workbook before the next row begins, so stopping
never loses a finished row; a row stopped mid-fill is restarted from scratch on
resume.

## Deliverables

- `excel_agent/agent/state_store.py`
  - `RunState`: versioned `run_state.json` — run id, config/mapping/input/copy
    paths, window info, layout fingerprint, pending row numbers, completed
    row outcomes, and stopped state,
  - atomic write (temp file + `os.replace`), validation on load.
- `excel_agent/agent/orchestrator.py`
  - `RunSession`: persists progress after every row,
  - `start_run` / `resume_run` / `validate_resume`: refuse to resume when the
    mapping, config, input workbook, or working copy changed,
  - `resume_rows`: skip every row that already has a workbook status,
  - `resume_preflight`: re-screenshot, re-inspect UIA, and re-resolve the
    trusted map before resuming; never calls Gemini just to resume.
- `excel_agent/agent/row_runner.py` gained `on_row` and `on_layout` callbacks
  so progress and layout fingerprints are recorded as the run proceeds.
- CLI: `python main.py run` writes run state; `python main.py resume`
  continues a stopped run. `run` refuses to start while a stopped run is
  recorded — use `resume`, or delete `data/state/run_state.json` to start a
  new run.
- 31 new tests.

## Demo

Stop after row 4, then resume to finish rows 5 and 6:

```powershell
..\.venv\Scripts\python.exe main.py calibrate --config config/agent.test.json --no-overlay
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json --no-overlay
# press Alt+T while row 5 is filling
..\.venv\Scripts\python.exe main.py resume --config config/agent.test.json --no-overlay
```

Observed:

```text
# run (stopped mid-run)
Workbook: ...\data\output\test_form_agent.xlsx
Pending rows: 3; processing: 3
Run state: ...\data\state\run_state.json

Row 4: save check: saved (the filled fields changed)
Row 4: DONE
Row 5: attempt 1/3 (4 field(s))
Stopped: stopped while filling the form

Processed 1 row(s): 1 DONE, 0 RECHECK
Row 4: DONE (attempt 1)
Stopped at row 5: stopped while filling the form
Run state: ...\data\state\run_state.json
AI calls: 0

# resume
Workbook: ...\data\output\test_form_agent.xlsx
Run state: ...\data\state\run_state.json
Recorded run: 20261108_123748 (stopped at row 5, 1 finished)
Rows to finish: 2

Preflight: 4 field(s) resolved; layout matches the recorded run
Preflight screenshot: ...\data\screenshots\resume_20261108_123809_311144_preflight.png

Row 5: save check: saved (the filled fields changed)
Row 5: DONE
Row 6: save check: saved (the filled fields changed)
Row 6: DONE

Processed 2 row(s): 2 DONE, 0 RECHECK
Row 5: DONE (attempt 1)
Row 6: DONE (attempt 1)
Run state: ...\data\state\run_state.json
AI calls: 0
```

The workbook showed rows 2, 4, 5, 6 as `DONE` with timestamps; row 3 stayed
`DONE` (calibration) and untouched. The fixture listbox held exactly four
records (`INV-0001`, `INV-0003`, `INV-0004`, `INV-0005`). Re-running `resume`
reported `Nothing left to process for this run.` Running `run` again while a
stopped run is recorded refused with exit code 1 and pointed at `resume`.

## Acceptance

- **State is saved on stop.** `run_state.json` is written at start, after every
  row, and at the end — atomically.
- **Resume re-screenshots and re-resolves the map.** Preflight re-inspects UIA
  and reports whether the layout changed; the workbook statuses are the source
  of truth, so completed rows are never touched again.
- **Current row restarts from scratch.** A row stopped mid-fill has no status,
  so `resume_rows` selects it again and `run_row` fills/saves it cleanly.
- **No row is saved twice.** Every finished row carries `DONE`/`RECHECK` in the
  workbook before the next row begins, and `resume_rows` skips every
  status-bearing row.
- **No unattended re-grounding.** Resume trusts the existing mapping; a
  structural layout change stops the resume with "run `setup` again."
- `pytest`: **271 passed** (31 new tests for Segment 8).
- `python main.py --check`: passed.

## Decisions

- **Workbook statuses, not run state, guard duplicates.** `run_state.json` says
  what the agent intended; the `Agent Status` column says what actually
  happened. Resume checks both, so a crash between Save and the status write
  still re-fills the row instead of losing it, and a DONE row is never
  re-saved.
- **One run-state file per run.** `run` writes the file when it starts and
  refuses to start while a stopped run is recorded — this prevents two runs
  from diverging on the same working copy.
- **Resume is read-only about the mapping.** It never calls Gemini just to
  resume; Gemini is reached only if a save itself is ambiguous, same as a
  fresh `run`.
- **Layout fingerprint is informational.** A changed fingerprint logs a note;
  only a structural problem (a mapped field missing) aborts the resume.

## Known limitations

- The working copy snapshots the input at the first `run`. Rows added to the
  input afterwards are not seen until `data/state/output.json` is deleted.
- `--limit` counts pending rows, not physical Excel rows.
- Stop after Save (during the post-save wait) leaves `RECHECK` with "the record
  may already exist"; resume does not retry it — this matches Segment 7's
  "never retry a save that may already exist" rule.
