# Post-save verification

You are given two screenshots of the same desktop form:

1. The **before** screenshot, taken after the row was filled in.
2. The **after** screenshot, taken after the Save button was clicked.

Before: {{PRE_WIDTH}}x{{PRE_HEIGHT}} pixels. After: {{POST_WIDTH}}x{{POST_HEIGHT}}
pixels.

Mapped fields and the values that were entered:

{{FIELDS}}

Decide whether the record was actually saved.

Positive evidence:

- the fields are cleared or reset,
- a success or confirmation message is visible,
- a new row or record appears in a list, table, or grid,
- a status line changes to a saved/confirmed state.

Negative evidence:

- the same values are still visible in the same fields,
- an error or validation message is visible,
- nothing on the screen indicates a successful save.

If the same text is still visible at the same coordinates and there is no clear
confirmation, treat the record as NOT saved.

Return ONE JSON object and nothing else. No markdown, no commentary.

## Rules

- `approved` may be true only when `saved` is true.
- `approved` must be false if `issues` is not empty.
- Be conservative: when the evidence is ambiguous, set `saved` and `approved`
  to false and explain what is missing.
- `form_cleared` is true when the mapped fields no longer contain the entered
  values, false when they still do, and null when that cannot be determined.

## Response schema

```json
{
  "approved": true,
  "saved": true,
  "confidence": 0.0,
  "form_cleared": true,
  "evidence": [],
  "issues": []
}
```
