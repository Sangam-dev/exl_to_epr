# Changelog

All notable changes to this project are recorded here. One entry per tagged
segment.

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
