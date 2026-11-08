# Pre-save verification

You are given one screenshot of a desktop form after an automation agent typed
one row into it. The screenshot is {{IMAGE_WIDTH}}x{{IMAGE_HEIGHT}} pixels.

Mapped fields and the values that should be present:

{{FIELDS}}

For every mapped field:

1. Locate the input control for that field.
2. Read the text currently visible inside the control.
3. Compare it with `expected`.

Report a problem when a value is missing, in the wrong field, duplicated,
truncated, or different from `expected`. An empty `expected` means the field
must be empty.

Return ONE JSON object and nothing else. No markdown, no commentary.

## Rules

- Return exactly one `field_checks` entry per mapped field, with no extras.
- `field_id` and `column` must match the mapped field exactly.
- `ok` is true only when the visible value matches `expected`.
- `approved` must be false if any check fails or if `issues` is not empty.
- Do not guess. If a field cannot be read, set `ok` to false and explain why.

## Response schema

```json
{
  "approved": true,
  "confidence": 0.0,
  "field_checks": [
    {
      "field_id": "f1",
      "column": "Invoice Number",
      "expected": "INV-0001",
      "observed": "INV-0001",
      "ok": true,
      "note": ""
    }
  ],
  "issues": []
}
```
