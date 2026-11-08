# Segment 5 - Mapping & approval

**Version:** `v0.6-mapping`
**Status:** implemented
**Date:** 2026-11-07

## Goal

Validate the grounded column -> field mapping and let the user approve it once,
so later segments reuse a saved map instead of asking Gemini again.

## Deliverables

- `excel_agent/ai/mapping.py`
  - `MappingEntry`, `ButtonEntry`, and `ApprovedMapping` models,
  - one-to-one checks (a column maps once, a field is assigned once),
  - duplicate detection, unknown column/field detection, type compatibility,
  - number-column checks against every Excel row,
  - confidence thresholds (`gemini.min_mapping_confidence`),
  - `required_columns` enforcement,
  - `save_mapping` / `load_mapping` with a version check.
- `excel_agent/agent/setup_flow.py`
  - `print_mapping` table,
  - `edit_mapping` with `set`, `remove`, `show`, `done`, and `cancel`,
  - `approve_mapping` with explicit warning acknowledgement,
  - `ApprovalOutcome` result object.
- `python main.py setup` with `--yes`, `--mapping`, and `--no-annotate`.
- `excel.required_columns` and `gemini.min_mapping_confidence` config options.
- Annotated setup screenshot in `data/setup/`.

## Demo

With the WinForms fixture open:

```powershell
..\.venv\Scripts\python.exe main.py setup --config config/agent.test.json --yes
```

Observed:

```text
Window: 0x3509D8 'Excel Agent Test Form'
UIA candidates: 15
Excel headers: 9 (agent status columns excluded)
Calling Gemini (gemini-3.6-flash)...

Proposed mapping (4 column(s)):
  Invoice Number               -> f1   Invoice Number               text    0.95
  Customer Name                -> f2   Customer Name                text    0.95
  Amount                       -> f3   Amount                       number  0.95
  Notes                        -> f4   Notes                        text    0.95

Grounded fields (4):
  f1   Invoice Number               text    (823, 310, 1248, 335)
  f2   Customer Name                text    (823, 360, 1248, 385)
  f3   Amount                       number  (823, 410, 1248, 435)
  f4   Notes                        text    (823, 460, 1248, 485)

Buttons (2):
  b1   Save                         save    (823, 523, 948, 552)
  b2   Clear                        cancel  (960, 523, 1085, 552)

Unmapped columns: Invoice Date, Payment Method, Internal Code, Currency, Total With Tax
Annotated: data/setup/setup_20261107_230521_annotated.png

Approved mapping saved: data/state/mapping.json
AI calls: 1
```

The interactive path was also run (piped `done` then `y`): the edit prompt
appeared, the mapping was accepted, and `data/state/mapping.interactive.json`
was written. A transient 503 on that run was recovered by the retry path.

The annotated screenshot showed all four red field boxes and both blue button
boxes on the fixture's controls.

## Acceptance

- Mapping cannot be approved if a required column is missing or a field is
  assigned twice: enforced by `validate_mapping` and `approve_mapping`; both
  cases are covered by unit tests.
- Approved mapping is saved and reused: `data/state/mapping.json` is written
  with a version, timestamp, rectangles, and UIA candidate IDs, and
  `load_mapping` validates it.
- Unmapped columns are explicitly listed: computed from the Excel headers and
  printed in the mapping table.
- `pytest`: 167 passed (36 new mapping tests).
- `python main.py --check`: passed.

## Decisions

- **`mapping.json` is authoritative.** Later segments read it instead of
  re-grounding. `config.fields` is not rewritten.
- **`required_columns` defaults to `[]`.** A project opts in to mandatory
  columns. The test config requires the three fields it expects.
- **Low confidence is a warning, not an error.** It needs an explicit
  `approve`; `--yes` counts as explicit approval.
- **Editing is one-to-one by construction.** `set` removes any existing entry
  for the same column or the same field before adding the new one.
- **Manual assignments get confidence `1.0`.** A human chose the pair.
- **Agent columns are excluded before validation.** `Agent Status`,
  `Agent Timestamp`, and `Agent Error` cannot be mapped to form fields.
- **`save_mapping` refuses unapproved maps.** An empty `approved_at` is a hard
  error, so a mapping cannot be persisted by accident.
- **Number columns are checked against every row.** A mapping is rejected if
  any pending value is not numeric; this prevents a failure halfway through a
  batch.

## Known limitations

- The editor is text-only; there is no graphical mapping UI.
- Column names containing leading/trailing spaces are matched as written in
  the workbook headers.
- `--yes` accepts warnings without showing the annotated screenshot; use the
  interactive path when confidence is low.
- The mapping is tied to the grounded layout. If the target window moves or
  changes, Segment 6's calibration must re-ground before using the rectangles.
