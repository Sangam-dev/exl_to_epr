# Segment 10 - Provable saves & duplicate-proof retries

**Version:** `v0.9.10-save-confirmation`
**Status:** implemented
**Date:** 2026-11-13

## Goal

Make every `DONE` provable: a row is successful only when configured success
text is read from the target window after Save. Make duplicate records
impossible by construction: once Save is clicked, the row is never clicked
again in the same run, and an interrupted or uncertain Save becomes `RECHECK`
instead of a retry.

## Deliverables

- `excel_agent/agent/confirmation.py`
  - explicit `CONFIRMED` / `REJECTED` / `UNKNOWN`,
  - `TextConfirmation(config, reader)` crops the configured success/error
    regions and matches patterns; error text takes precedence,
  - injectable `TextReader` protocol; `GeminiTextReader` is the only real
    reader, backed by `config/prompts/read_region_text.md`,
  - reader/transport failures become `UNKNOWN`, never `CONFIRMED`.
- `excel_agent/config.py`
  - `NormalizedRegion` (0-1000 coordinates) and `ConfirmationConfig`
    (`success_region`, `error_region`, `success_pattern`, `error_patterns`),
  - invalid regular expressions are rejected while loading config,
  - `safety.stop_on_failure` now defaults to `true`.
- `excel_agent/agent/state_store.py`
  - `SaveIntent(row_number, invoice_id, state="attempting_save", ts)`,
  - `RunState.save_intent`, serialized and validated with the rest of the run
    state.
- `excel_agent/agent/orchestrator.py`
  - `RunSession.begin_save` writes the intent before the click,
  - `RunSession.record` clears the intent only for the matching row,
  - `validate_resume` rejects an intent outside the run or for a finished row,
  - `reconcile_interrupted_save` marks the row `RECHECK` with
    `interrupted around Save; verify in target app` and never retries it.
- `excel_agent/agent/pending_status.py`
  - atomic `data/state/pending_status.json` sidecar,
  - `write_status_with_retries` with exponential backoff,
  - `apply_pending_statuses` replays queued outcomes before any row is read.
- `excel_agent/agent/row_runner.py`
  - removed the pixel verdict path (`classify_save`, `NOT_SAVED`, `SAVED`,
    `AMBIGUOUS`),
  - confirmation strategy after Save; pixel comparison is a `weak_hint` only,
  - `on_before_save`, `confirm_each` / `input_func`, `pending_status_path`,
    `status_attempts`, and `RowOutcome.status_written`,
  - `run_rows` stops before the next row when a status could not be written.
- `excel_agent/agent/calibration.py`
  - same confirmation strategy and status retry/sidecar behavior,
  - `CalibrationResult.status_written`.
- CLI
  - `run --confirm-each` pauses for Enter/Y before each Save,
  - `run` applies pending statuses before reading the workbook,
  - `resume` reconciles an interrupted save before selecting rows,
  - a fresh `run` refuses to archive or restart while an unresolved save intent
    exists.
- Example/test configs and the wizard-generated config include the new
  `confirmation` block; `stop_on_failure` is `true`.

## Behavior

After Save, exactly one outcome is chosen:

| Outcome | Evidence | Workbook |
|---|---|---|
| `CONFIRMED` | success pattern matched in the success region | `DONE` |
| `REJECTED` | an error pattern matched in the error region | `RECHECK` |
| `UNKNOWN` | missing text, no pattern configured, or reader failure | `RECHECK` with `record may already exist` |

Pixel changes are logged as a hint and can never produce `DONE`. If no
confirmation pattern is configured the agent fails closed: every saved row is
`UNKNOWN` and therefore `RECHECK`.

Write-ahead flow for one row:

```text
fill -> UIA read-back -> pre-save screenshot -> [--confirm-each prompt]
     -> run_state: save_intent = attempting_save
     -> Save click
     -> confirmation (CONFIRMED / REJECTED / UNKNOWN)
     -> status write (retry/backoff)
     -> run_state: completed row, intent cleared
```

If the process dies after the intent is written, the next `resume` sees the
unresolved marker, writes `RECHECK` with
`interrupted around Save; verify in target app`, records the row as finished,
and continues with the next row. If the status write fails after Save, the
outcome is persisted in the run state and the sidecar, the run stops, and the
next run replays the sidecar before touching any row.

## Acceptance

- A Save is clicked at most once per row; the runner has no post-Save retry
  path.
- `DONE` is unreachable without a `CONFIRMED` text match.
- Pixel-unchanged and pixel-changed screenshots both fail closed to `RECHECK`.
- An interrupted save intent is converted to `RECHECK`, not retried.
- A fresh `run` cannot archive a run with an unresolved intent.
- Pending statuses are applied before `read_workbook`; a remaining failure
  stops the command.
- `run --confirm-each` prompts after read-back and before the intent/Save;
  declining raises `RunnerAborted` and leaves the row unsaved.
- `pytest tests/unit`: **334 passed**.
- Full `pytest`: all unit tests pass; the 3 integration tests still fail in this
  environment because their subprocess `cwd="desktop_vision_agent"` is resolved
  relative to a directory that already ends in `desktop_vision_agent`
  (pre-existing, unrelated to this segment).

## Decisions

- **Text is the only positive evidence.** Screen changes are too ambiguous:
  both "form cleared" and "record added" can change pixels. Configured success
  text is a direct statement from the target application.
- **Fail closed.** A missing pattern or unreadable region is `UNKNOWN`, never a
  guess. The user sees `RECHECK` and decides.
- **Write-ahead instead of post-hoc inference.** The intent is flushed before
  the click, so a crash at any point leaves an unambiguous "this row may have
  been saved" marker.
- **No post-Save retry, ever.** A duplicate business record is worse than a
  manual check. Retries only cover fill and read-back failures before the click.
- **Sidecar for status durability.** The workbook may be locked by Excel; the
  saved outcome must survive the process, so it is recorded twice (run state
  and sidecar) and replayed first on the next command.
- **Calibration uses the same strategy.** A calibrated map is trusted only via
  the same explicit evidence, so calibration cannot be easier to pass than a
  batch row.

## Known limitations

- `confirmation` must match the target application. The example config is a
  starting point; a wrong region or pattern produces `RECHECK`, never a false
  `DONE`.
- OCR is still deferred. `GeminiTextReader` is the only real reader; an OCR
  implementation can satisfy `TextReader` later without changing callers.
- The wizard generates an empty confirmation block, so its automatic first run
  fails closed until the user fills in the region/patterns.
- A Save that the application accepts but shows no configured text remains
  `RECHECK` by design.
- Pending statuses are keyed by row number; editing the workbook between runs
  can move rows, so restore the recorded working copy before resuming.
