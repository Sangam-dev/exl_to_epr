# Segment 0 - Foundation & version control

**Version:** `v0.1-foundation`
**Status:** implemented
**Date:** 2026-11-06

## Goal

Create a clean, trustworthy project skeleton before any automation: version
control, typed config, secret-safe logging, and an environment check.

## Deliverables

- Git repository at the workspace root with `.gitignore`.
- `excel_agent/` package skeleton with module stubs for every later segment.
- `excel_agent/config.py` with Pydantic v2 models and one-to-one mapping
  validation.
- `excel_agent/logging_setup.py` with secret redaction and a rotating file log.
- `excel_agent/cli.py` with `--check` and `--version`; future subcommands are
  registered and report their target segment.
- `config/agent.example.json` and draft prompts.
- `tests/unit/test_config.py` and `tests/unit/test_logging_setup.py`.
- `new.py` moved to `legacy/prototypes/new.py`; hardcoded key removed and
  replaced with `GEMINI_API_KEY` from `.env`.

## Demo

```powershell
cd desktop_vision_agent
..\.venv\Scripts\python.exe main.py --check
```

## Acceptance

- No API key in source.
- Config loads and validates.
- `--check` reports green.
- First commit tagged `v0.1-foundation`.

## Decisions

- Repo root is the workspace root (`exel_automaiton/`); the application lives in
  `desktop_vision_agent/`.
- Config JSON uses `extra="forbid"` so typos fail loudly.
- The Gemini key is read from the environment only; `--check` shows a masked
  form.
- Missing dependencies are critical failures; missing git or data folders are
  warnings.

## Test results

`python main.py --check`:

```text
Result: PASSED - environment is ready
```

All checks green: Windows 11, Python 3.12.2, pydantic 2.14.3, pandas 3.1.1,
openpyxl 3.1.5, google-genai 3.0.0, win32gui, comtypes 1.4.17,
pywinauto 0.6.9, pyperclip 1.11.0, `.env` key loaded (masked), config valid,
data folders writable, git repository present.

`python -m pytest`:

```text
16 passed in 0.51s
```

`python -m compileall -q excel_agent`: passed.

Secret scan of tracked text files: no credentials outside the intentional fake
values in `tests/unit/test_logging_setup.py`.

## Known limitations

- All automation modules are docstring stubs.
- `--check` validates the example config, not a real target application.
- `config/agent.example.json` points at placeholder paths and an example app
  title; it must be replaced with real values in later segments.
