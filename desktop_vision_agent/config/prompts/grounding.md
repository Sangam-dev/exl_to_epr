# Grounding prompt (draft - implemented in Segment 4)

You are given:

1. one screenshot of a data-entry form,
2. the UI Automation candidates for that window (id, name, control type, bounds),
3. the Excel column headers.

Return **only** JSON matching the agreed schema.

Rules:

- Identify text inputs, number inputs, and the Save button.
- Ignore dropdowns, checkboxes, date pickers, and tabs (out of scope for v1.0).
- Use normalised bounding boxes on a 0-1000 scale, origin top-left.
- Prefer a UIA candidate when one clearly matches the visual field.
- Map each Excel column to at most one field; never map a field twice.
- Leave unmappable columns in `unmapped_columns` and explain why.
- Report `layout_confidence` between 0 and 1.
- Do not invent fields that are not visible.
