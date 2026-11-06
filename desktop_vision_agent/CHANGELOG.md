# Changelog

All notable changes to this project are recorded here. One entry per tagged
segment.

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
