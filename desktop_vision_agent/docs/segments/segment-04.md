# Segment 4 - Gemini grounding

**Version:** `v0.5-ai-grounding`
**Status:** implemented
**Date:** 2026-11-07

## Goal

Send one screenshot, the window's UIA controls, and the Excel headers to
Gemini, and get back a validated field map with screen coordinates.

## Deliverables

- `excel_agent/ai/client.py`
  - `GeminiClient` built from `GEMINI_API_KEY` only,
  - bounded retries with exponential backoff (1s, 2s, 4s, capped at 8s),
  - JSON extraction from plain, fenced, or prose-wrapped output,
  - a hard `max_ai_calls` budget counted per request (retries included),
  - one JSON log per request in `data/ai_logs`, with credentials redacted.
- `excel_agent/ai/prompts.py` + `config/prompts/grounding.md`
  - the prompt is a versioned Markdown file,
  - `{{TOKEN}}` placeholders are replaced literally, so the JSON schema braces
    in the prompt survive rendering.
- `excel_agent/ai/grounding.py`
  - strict Pydantic models for the response,
  - cross-field validation: unique IDs, known columns/fields/UIA candidates,
    one-to-one mapping, boxes inside the captured window,
  - every element is resolved to a physical screen rectangle; the UIA rectangle
    wins when the model names a candidate,
  - computed `unmapped_columns` / `unmapped_fields` are authoritative.
- `excel_agent/desktop/coordinates.py`
  - normalized `0-1000` -> image pixels and -> screen pixels,
  - bounds and ordering checks.
- `excel_agent/utils/images.py`
  - `prepare_image` downscales to `max_image_dimension`,
  - `annotate_boxes` draws labelled field/button boxes.
- `python main.py ground` with `--no-annotate`.
- `gemini.min_layout_confidence` config option (default `0.6`).

## Demo

With the WinForms fixture open:

```powershell
..\.venv\Scripts\python.exe main.py ground --config config/agent.test.json
```

Observed (first attempt hit a transient 503 and the retry succeeded):

```text
Gemini call failed (attempt 1/4): 503 UNAVAILABLE ...; retrying in 1s
Window: 0x4109D6 'Excel Agent Test Form'
UIA candidates: 15
Excel headers: 9 (agent status columns excluded)
Calling Gemini (gemini-3.6-flash)...

Layout confidence: 0.95
AI calls: 2

Fields (4):
  f1   Invoice Number             text    W2    (823, 310, 1248, 335)
  f2   Customer Name              text    W4    (823, 360, 1248, 385)
  f3   Amount                     number  W6    (823, 410, 1248, 435)
  f4   Notes                      text    W8    (823, 460, 1248, 485)
Buttons (2):
  b1   Save                       save    W9    (823, 523, 948, 552)
  b2   Clear                      cancel  W10   (960, 523, 1085, 552)
Column mapping (4):
  Invoice Number           -> f1 Invoice Number           0.98
  Customer Name            -> f2 Customer Name            0.98
  Amount                   -> f3 Amount                   0.98
  Notes                    -> f4 Notes                    0.98
Unmapped columns: Invoice Date, Payment Method, Internal Code, Currency, Total With Tax
Unmapped fields: -
Saved: data/grounding/grounding_20261107_224724.json
Annotated: data/grounding/grounding_20261107_224724_annotated.png
```

The annotated screenshot showed every red field box and blue button box
aligned with the fixture's controls.

## Acceptance

- Valid JSON matching the schema: enforced by Pydantic; invalid responses are
  rejected before any coordinates are used.
- Every field box is inside the target window: checked with `rect_within`.
- UIA candidate IDs are matched when available: unknown IDs are a hard error,
  and the UIA rectangle is preferred over the visual box.
- Unmapped columns are reported: computed from the Excel headers and printed.
- `pytest`: 131 passed.
- `python main.py --check`: passed.

## Decisions

- **One call per grounding run.** The model is not called per field or per row.
- **UIA rectangles win.** The model's box is used to confirm the candidate, not
  to override it. A visual box that does not overlap its candidate adds a
  warning and the UIA rectangle is still used.
- **Strict validation before use.** Unknown columns, unknown fields, duplicate
  mappings, unknown candidates, and out-of-window boxes are errors. They are
  reported together, not one at a time.
- **Agent columns are not mapping candidates.** `Agent Status`,
  `Agent Timestamp`, and `Agent Error` are removed from the headers sent to the
  model, so it cannot propose writing into the agent's own bookkeeping.
- **The call budget counts retries.** A retry is a real API request, so it
  consumes budget.
- **Low confidence is a warning, not a failure.** Segment 5 turns it into an
  approval requirement; Segment 4 only reports it.
- **Images are downscaled before sending.** Normalized coordinates are
  resolution-independent, so resizing does not change the geometry.

## Known limitations

- The model can still return a plausible box for the wrong control; validation
  checks structure, not semantic correctness. Segment 6's pre/post-save checks
  are the semantic guard.
- `role` for non-save buttons is advisory; only `save` will be required.
- The prompt is English-only and assumes a left-to-right form.
- A response with a correct schema but a low `layout_confidence` still produces
  a usable result; the caller must honor the warning.
