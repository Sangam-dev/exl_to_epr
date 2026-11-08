# Segment 9 - Hardening & release

**Version:** `v1.0`
**Status:** implemented
**Date:** 2026-11-08

## Goal

Harden the agent codebase with live integration testing against test fixtures,
provide reproducible offline demonstrations without API costs, document
operational best practices and troubleshooting, and finalize the `v1.0` release.

## Deliverables

- `tests/integration/`
  - `test_capture.py`: live window capture, DPI scaling validation, coordinate
    translations (`to_screen`), and invalid window error handling.
  - `test_uia.py`: live WinForms inspection, candidate extraction, candidate
    lookup, focus, and value read-back.
  - `test_fixture_form.py`: end-to-end integration with the test form fixture;
    validates field discovery, required-field save validation, and clear action.
- `scripts/`
  - `scripts/test_form.py`: Tkinter test fixture matching the WinForms form,
    supporting DPI-aware execution without PowerShell execution policy limits.
  - `scripts/test_form.ps1`: updated with Per-Monitor V2 DPI awareness and
    synchronized accessibility metadata for dynamic status updates.
  - `scripts/demo_offline.py`: zero-cost end-to-end demonstration script
    executing grounding, mapping approval, calibration, deterministic batch
    entry, and resume verification.
- Documentation:
  - `README.md` updated with full `v1.0` feature set, demo instructions,
    troubleshooting, and implemented vs. deferred matrix.
  - `CHANGELOG.md` updated with `[v1.0]` release notes.
- Package version bumped to `1.0.0` in `pyproject.toml` and `excel_agent/__init__.py`.

## Demo

Run full integration and unit test suite:

```powershell
pytest
```

Run the offline zero-cost demonstration:

```powershell
python scripts/demo_offline.py
```

Run batch entry against the test form fixture:

```powershell
# In terminal 1: launch fixture form
powershell -STA -ExecutionPolicy Bypass -File scripts\test_form.ps1

# In terminal 2: run calibration and batch entry
python main.py calibrate --config config/agent.test.json --no-overlay
python main.py run --config config/agent.test.json --limit 5 --no-overlay
```

## Acceptance

- **All offline tests pass**: 278 passed in ~16s across unit and integration tests.
- **Fixture-form demo works**: `scripts/demo_offline.py` executes all stages
  (setup, calibration, batch, resume) without requiring an API key.
- **Docs match actual behavior**: README accurately describes all 10 CLI commands,
  safety hotkeys, and troubleshooting steps.
- **Known limitations documented**: deferred controls (dropdowns, checkboxes,
  tabs) and modal dialog requirements documented in README and plan.

## Decisions

- **DPI awareness on fixtures**: Set Per-Monitor V2 DPI awareness in both
  Tkinter and WinForms test fixtures to ensure exact physical pixel alignment
  without bitmap virtualization.
- **Zero-cost demo runner**: `scripts/demo_offline.py` operates entirely
  in-memory and against temporary workspaces, allowing rapid evaluation in CI
  or offline developer environments without API costs.
