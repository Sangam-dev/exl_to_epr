# Changelog

All notable changes to this project are recorded here. One entry per tagged
segment.

## [Unreleased]

### Fixed

- A save is never clicked twice when the post-save comparison sees unchanged
  fields. Both `calibrate` and `run` now mark the row `RECHECK` with "the record
  may already exist" instead of retrying, so forms that keep their values after
  saving cannot create duplicate records.
- `RECHECK` rows are skipped by default (`excel.skip_statuses` now defaults to
  `["DONE", "RECHECK"]`), so a follow-up `run` cannot silently reprocess rows
  that were flagged for human review.
- `safety.stop_on_failure` now records the run as stopped (`stopped`,
  `stopped_row`, `stop_reason`) so the remaining rows can be resumed instead of
  appearing complete.
- `run` checks for a stopped run *before* opening the working copy, so a
  refused run can no longer replace `data/state/output.json`. A run state that
  can never be resumed (config/mapping/input changed, or the mapping was
  re-approved) is archived as `run_state.stale-<stamp>.json` and the copy it
  recorded is kept, instead of blocking every future run.
- `resume` reuses the exact working copy recorded in the run state even when
  `output.json` points elsewhere, and repairs the pointer.
- A missing working copy now blocks with an actionable error instead of
  silently copying the input workbook again (which would lose every DONE or
  RECHECK status and risk duplicate records).
- The wizard writes `skip_statuses` with both `DONE` and `RECHECK`.

## [v1.0] - 2026-11-08

### Added

- `tests/integration/`: live integration test suite for window capture (`test_capture.py`), UI Automation element tree inspection (`test_uia.py`), and test fixture form save/clear actions (`test_fixture_form.py`).
- `scripts/test_form.py`: Tkinter test fixture matching the WinForms form, supporting PerMonitorV2 DPI awareness.
- `scripts/demo_offline.py`: zero-API-cost demonstration script running the full agent lifecycle (grounding, mapping approval, calibration, batch runner, and resume check).
- High-DPI awareness (Per-Monitor V2) enabled across PowerShell WinForms and Tkinter test fixtures.
- Comprehensive troubleshooting and implemented vs. deferred feature matrix in `README.md`.

### Changed

- Package version bumped to `1.0.0` in `pyproject.toml` and `excel_agent/__init__.py`.
- Synchronized accessibility names in `test_form.ps1` for real-time UIA status verification.

## [v0.9-resume] - 2026-11-08

### Added

- `agent/state_store.py`: atomic `run_state.json` — run id, config/mapping/input/copy paths, window info, layout fingerprint, pending row numbers, completed row outcomes, and stopped state; versioned and validated on load.
- `agent/orchestrator.py`: `RunSession` that persists progress after every row, `start_run` for fresh batches, `resume_run`/`validate_resume` for continuing a stopped run, `resume_rows` to skip every status-bearing row, and `resume_preflight` to re-screenshot, re-inspect UIA, and re-resolve the trusted map before resuming.
- `agent/row_runner.py` gained `on_row` and `on_layout` callbacks so progress and layout fingerprints are recorded as the run proceeds.
- `python main.py resume` (and `run --state`): continue a stopped run with no duplicates. `run` refuses to start while a stopped run is recorded; resume re-reads UIA but never calls Gemini unless the save itself is ambiguous.
- 31 new tests (state store, orchestrator, and runner callbacks).

### Changed

- `run` writes the run-state file at start and after each row; `resume` finishes it at the end.

## [v0.8-batch] - 2026-11-08

### Added

- `agent/row_runner.py`: per-row fill/save loop over the trusted map,
  deterministic pixel-diff save classification, Gemini only for ambiguous
  saves, safe retry policy, layout fingerprint logging, per-row status writes,
  and optional screenshots on failure.
- `agent/workspace.py`: a recorded working copy (`data/state/output.json`) so
  calibration and the batch runner reuse the same workbook copy and a
  calibrated `DONE` row is never processed twice.
- `python main.py run` with `--config`, `--mapping`, `--limit`, and
  `--no-overlay`. The command refuses to start unless the mapping is trusted.
- `RunResult` / `RowOutcome` summaries and a per-run count of `DONE` and
  `RECHECK` rows.
- 25 new tests (workspace selection and batch runner paths).

### Changed

- `calibrate` now opens the recorded working copy instead of creating a new
  output file on every run.
- `run` uses `safety.stop_on_failure` to decide whether to continue after a
  `RECHECK` row.

## [v0.7-calibration] - 2026-11-08

### Added

- `models/verification.py`: `FieldCheck`, strict pre-save and post-save
  response schemas, verdicts, and `VerificationError`.
- `ai/verification.py`: pre-save and post-save prompt builders, strict parsers
  with cross-field checks, and Gemini wrappers.
- `config/prompts/pre_save.md` and `config/prompts/post_save.md`.
- `agent/calibration.py`: live UIA resolution, deterministic fill, UIA
  read-back, pre-save Gemini check, Save click, post-save pixel-diff veto,
  post-save Gemini check, safe retry policy, `DONE`/`RECHECK` writing, and
  trust persistence.
- `python main.py calibrate` with `--mapping` and `--no-overlay`.
- `trusted` and `trusted_at` fields in `data/state/mapping.json`;
  `mark_trusted` persists the calibration result.
- Gemini client multi-image support (`generate_json_images`) and an
  `on_attempt` heartbeat for watchdog-safe retries.
- 40 new tests (verification, calibration helpers, client images/heartbeat,
  and calibration loop paths).

### Changed

- The calibration row is marked `RECHECK` instead of retrying a save that may
  already have succeeded; this prevents duplicate records.
- A stop after Save is recorded as `RECHECK`; a stop before Save leaves the
  row pending.

## [v0.6-mapping] - 2026-11-07

### Added

- `ai/mapping.py`: approved-mapping models, one-to-one and duplicate checks,
  type compatibility, number-column value checks, confidence thresholds, and
  `mapping.json` save/load with version validation.
- `agent/setup_flow.py`: mapping table printing, interactive `set`/`remove`/
  `show`/`done`/`cancel` editing, and explicit approval (warnings need
  `approve`; errors block approval).
- `python main.py setup` with `--yes`, `--mapping`, and `--no-annotate`; writes
  the approved mapping to `data/state/mapping.json` and an annotated screenshot
  to `data/setup/`.
- `excel.required_columns` and `gemini.min_mapping_confidence` config options.
- 36 mapping tests (validation, build, persistence, approval, editing).

### Changed

- `ground` and `setup` share the target-opening and header-filtering helpers.
- `data/setup/*` is gitignored.

## [v0.5-ai-grounding] - 2026-11-07

### Added

- `ai/client.py`: Gemini client with bounded retries and exponential backoff,
  JSON extraction from plain/fenced/embedded output, per-call request logs in
  `data/ai_logs` with credential redaction, and a hard call budget.
- `ai/prompts.py` + `config/prompts/grounding.md`: versioned, editable grounding
  prompt with `{{TOKEN}}` placeholders.
- `ai/grounding.py`: strict Pydantic schema, cross-field validation (one-to-one
  mapping, known columns/fields/candidates, boxes inside the window), and
  resolution of every element to a screen rectangle.
- `desktop/coordinates.py`: normalized `0-1000` -> image/screen conversion and
  bounds checks.
- `utils/images.py`: `prepare_image` downscaling and generic `annotate_boxes`.
- `python main.py ground` with `--no-annotate`; writes
  `data/grounding/grounding_<stamp>.json` and an annotated screenshot.
- `gemini.min_layout_confidence` config option.
- 36 grounding tests (coordinates, JSON extraction, schema, client retries,
  budget, redaction, prompts).

### Changed

- Agent-owned status columns are excluded from the headers sent to Gemini.
- `annotate_candidates` now shares the drawing helper with `annotate_boxes`.

## [v0.4-safety] - 2026-11-07

### Added

- `desktop/input.py`: Unicode keyboard input, key/chord presses, mouse clicks,
  clipboard paste, and a private `dwExtraInfo` tag on every agent event.
- `safety/hotkeys.py`: `Alt+T` / `Alt+P` / `Alt+R` parsing, validation, and
  matching.
- `safety/input_lock.py`: `WH_KEYBOARD_LL` and `WH_MOUSE_LL` hooks on a
  dedicated thread. Blocks external input, allows agent-tagged events and the
  three shortcuts, and supports passthrough mode for pause.
- `safety/controller.py`: running/paused/stopped state machine with a hotkey
  action queue, `check()`/`wait()` helpers, and clean shutdown.
- `safety/watchdog.py`: releases the lock when the automation loop stops
  heartbeating.
- `safety/overlay.py`: always-on-top status window with the active shortcuts.
- `python main.py safety test` with `--seconds` and `--no-overlay`.
- Unit tests for hotkey parsing, keyboard decisions, tagged input, controller
  transitions, pause/resume wakeups, and the watchdog.

### Fixed

- Hotkey key-ups are allowed through so a blocked `Alt` key-up cannot leave the
  modifier stuck after resume.

## [v0.3-desktop] - 2026-11-07

### Added

- `desktop/window.py`: visible-window discovery by title and process name,
  ambiguity detection, DPI awareness, window/client geometry, and a focus path
  with the AttachThreadInput fallback.
- `desktop/capture.py`: target-window capture via `PrintWindow` with
  `PW_RENDERFULLCONTENT`, plus an `ImageGrab` fallback for minimized captures,
  blank output, and DPI-virtualized black padding. Includes the image-to-screen
  transform.
- `desktop/uia.py`: pywinauto UIA inspection limited to the target window, with
  native focus/read/invoke helpers.
- `models/screen.py`: `Rect`, `WindowInfo`, and `UIACandidate`.
- `utils/images.py`: annotated screenshots for checking UIA alignment.
- `python main.py inspect` command with `--max-elements` and `--no-annotate`.
- `scripts/test_form.ps1` WinForms fixture and `config/agent.test.json`.
- Unit tests for geometry, window matching, capture transforms, DPI padding
  detection, and annotation.

## [v0.2-excel] - 2026-11-07

### Added

- pandas workbook reader with exact Excel row numbers, header normalisation,
  duplicate-header detection, required-column checks, and pending-row filtering.
- Text/number value formatting and per-row validation (empty/duplicate IDs,
  invalid numbers).
- openpyxl status writer: copies the input, reuses or adds `Agent Status`,
  `Agent Timestamp`, and `Agent Error`, and saves atomically after every row.
- `scripts/make_sample_excel.py` sample workbook.
- `python main.py excel inspect` command.
- Unit tests for reader, writer, and CLI wiring.

### Security

- `data/input/` is now gitignored so real workbooks are never committed.

## [v0.1-foundation] - 2026-11-06

### Added

- Project skeleton `excel_agent/` with stubs for the Excel, desktop, AI, agent,
  safety, and utility layers.
- Pydantic v2 configuration models with one-to-one mapping validation
  (`excel_agent/config.py`).
- Secret-redacting logging with a rotating file handler
  (`excel_agent/logging_setup.py`).
- CLI environment check: `python main.py --check`.
- Example configuration and draft Gemini prompts.
- Unit tests for config validation and secret redaction.
- Per-segment documentation in `docs/segments/`.

### Changed

- `new.py` moved to `legacy/prototypes/new.py`; the hardcoded Gemini key was
  removed in favour of `GEMINI_API_KEY` from `.env`.

### Security

- `.env` and runtime data are gitignored.
- Log records are scrubbed of API keys and tokens before output.
