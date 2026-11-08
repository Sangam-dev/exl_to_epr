# Segment 6 - First-row calibration

**Version:** `v0.7-calibration`
**Status:** implemented
**Date:** 2026-11-08

## Goal

Prove the approved coordinate map on the first pending row before trusting it:
fill the form, verify the values with Gemini, save, and verify the save.

## Deliverables

- `excel_agent/models/verification.py`
  - `FieldCheck`, `PreSaveResponse`, `PostSaveResponse`,
  - `PreSaveVerdict`, `PostSaveVerdict`,
  - `VerificationError`.
- `excel_agent/ai/verification.py`
  - strict pre-save and post-save parsers,
  - cross-field checks (one check per mapped field, no unknown or duplicate
    fields, echoed expected values, `approved` must agree with `saved`),
  - prompt builders and Gemini wrappers.
- `config/prompts/pre_save.md` and `config/prompts/post_save.md`.
- `excel_agent/agent/calibration.py`
  - live UIA resolution by candidate id and control name,
  - deterministic fill with click + `Ctrl+A` + paste/delete,
  - UIA read-back before the screenshot check,
  - pre-save screenshot + Gemini check,
  - Save click, post-save screenshot + Gemini check,
  - pixel-diff veto over the non-empty mapped fields,
  - retry policy that never retries a save that may already have succeeded,
  - `DONE` / `RECHECK` writing and trust persistence.
- `python main.py calibrate` with `--config`, `--env-file`, `--mapping`, and
  `--no-overlay`.
- `generate_json_images` and an `on_attempt` heartbeat in the Gemini client.
- `trusted` / `trusted_at` fields in `data/state/mapping.json`.
- 40 new tests (verification, calibration helpers, client images/heartbeat,
  calibration loop paths).

## Demo

With the WinForms fixture open:

```powershell
..\.venv\Scripts\python.exe main.py calibrate --config config/agent.test.json --no-overlay
```

Observed:

```text
Workbook: ...\data\input\invoices.xlsx
Pending rows: 4; calibration row: 2
Mapping: ...\data\state\mapping.json (trusted=False, 4 column(s))
Window: 0x30926 'Excel Agent Test Form'
Output copy: ...\data\output\test_form_agent_20261108_113801.xlsx
Gemini: gemini-3.6-flash
  Alt+T stop | Alt+P pause | Alt+R resume

Row 2: attempt 1/3 (4 field(s))
  pre-save screenshot: ...\calibrate_20261108_113804_734761_row2_attempt1_pre_save.png
  post-save screenshot: ...\calibrate_20261108_113810_232818_row2_attempt1_post_save.png (the filled fields changed)
Row 2: DONE (map trusted=True)

Row 2: DONE
Attempts: 1
Map trusted: True
Screenshot: ...pre_save.png
Screenshot: ...post_save.png
AI calls: 2
Output workbook: ...\test_form_agent_20261108_113801.xlsx
```

The pre-save screenshot showed `INV-0001`, `Acme Corp`, `1250`, and
`First invoice` in the four fields. The post-save screenshot showed cleared
fields, `Status: saved 11:38:16`, and one listbox record. The output workbook
row 2 was `DONE` with a timestamp; row 3 stayed `DONE` and was skipped. The
fixture contained exactly one saved record.

## Acceptance

- **Map stays untrusted until both checks pass.** `ApprovedMapping.trusted`
  defaults to `false`; only a pre-save approval followed by a post-save
  approval calls `mark_trusted(..., trusted=True)`. The live run wrote
  `"trusted": true` and `"trusted_at": "2026-11-08T11:38:16+05:45"`.
- **On failure the row becomes `RECHECK` and the map stays untrusted.**
  Covered by `test_calibrate_row_rechecks_when_pre_save_is_rejected`,
  `test_calibrate_row_does_not_retry_an_uncertain_save`, and
  `test_calibrate_row_retries_when_the_fields_are_unchanged`.
- **Pre-save and post-save screenshots are saved.** Every attempt writes both
  images to `data/screenshots/` with the row, attempt, and phase in the name.
- `pytest`: 207 passed (40 new tests).
- `python main.py --check`: passed.

## Decisions

- **Trust is part of the mapping file.** `trusted` and `trusted_at` are
  optional fields with safe defaults, so older `mapping.json` files still
  load. `mark_trusted` refuses to persist an unapproved mapping.
- **Live UIA first, saved rectangle second.** The calibration loop re-inspects
  the window on every attempt. A candidate id is used only when its control
  name still matches; otherwise the control is found by name. If neither
  works, the saved rectangle is used when it is inside the current window;
  an outside-window rectangle is a hard layout problem.
- **UIA read-back is a cheap pre-check.** It catches a failed paste before a
  Gemini call. If a control does not expose a value, the screenshot check is
  still authoritative.
- **The pixel diff is a veto, not a second opinion.** If every non-empty
  mapped field is pixel-identical before and after Save, the row is treated
  as not saved and retried. Empty fields are ignored.
- **Retry only when a retry is safe.** A pre-save rejection retries. An
  unchanged form after Save retries. If the fields changed but Gemini is not
  convinced, the row is marked `RECHECK` immediately: the record may already
  exist, and a retry could duplicate it.
- **A stop after Save is uncertain.** The row is marked `RECHECK` with an
  explanation. A stop before Save leaves the row pending.
- **Two images, one call.** The post-save check sends the before and after
  screenshots in a single request. `on_attempt` heartbeats the watchdog before
  every retry so a slow Gemini call cannot trip the 120s watchdog.

## Known limitations

- An app that keeps the old values after a successful save and only shows a
  toast will be classified as not saved by the pixel-diff veto. Increase
  `actions.wait_after_save_seconds` or adjust the heuristic for such an app.
- There is no layout fingerprint or automatic re-grounding yet; a moved window
  is handled by live UIA resolution, but a changed control layout needs
  `setup` again.
- There is no run-state file yet. Stop/resume across rows is Segment 8.
- The post-save screenshot is taken after `actions.wait_after_save_seconds`;
  slow applications need a larger value.
