# Pre-save verification prompt (draft - implemented in Segment 6)

Given:

- the screenshot after filling the form,
- the Excel row values,
- the approved column -> field mapping,

check every mapped field: is the correct value present in the correct field?

Report missing, duplicated, truncated, or misplaced values.

Return JSON:

```json
{ "approved": true, "issues": [], "field_checks": [] }
```

Do not approve if any mapped value is missing or in the wrong field.
