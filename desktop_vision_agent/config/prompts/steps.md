# One step, in the user's own words

You are given:

1. A screenshot of one desktop application window ({{IMAGE_WIDTH}}x{{IMAGE_HEIGHT}} pixels).
2. A list of the UI Automation (UIA) controls found in that window.
3. The column headers of an Excel workbook.
4. One instruction, written by the user in plain words, and possibly a correction.

The user is teaching an agent to enter one record in this form. The agent can
do exactly two things: click a button, and type text into a text field. Your
job is to turn the instruction into those actions, in the order to do them.

Return ONE JSON object and nothing else. No markdown, no commentary.

## The instruction

{{INSTRUCTION}}

## The correction

{{CORRECTION}}

Apply the correction on top of the instruction. When the correction is
`(none)`, the instruction stands as written.

## UIA controls

{{CANDIDATE_COUNT}} control(s) were found. A control with `type=Button` can be
clicked. Controls with `type=Edit`, `Document` or `Spinner` accept typed text.

{{CANDIDATES}}

## Excel columns

{{HEADERS}}

## Rules

- `kind` is `click` when the instruction clicks a button, and `fill` when it
  types data into one or more text fields.
- For `click`, `button` must be the exact `name` of one `Button` control from
  the list above. Copy the name; never invent, shorten or translate it.
- For `fill`, list one entry per field the instruction types into, in the order
  the agent should type. `field` must be the exact `name` of one text control
  from the list above. `column` must be one of the Excel column headers above,
  spelled exactly.
- Fill only the fields the instruction asks for. A partial fill is correct:
  "fill the invoice number" fills one field, while "fill up all the form"
  fills every field that has a matching column.
- Never list the same field twice, and never list the same column twice.
- The screenshot may already show data typed by earlier steps. Trust the
  instruction, not the current contents of the boxes.
- Dropdowns, combo boxes, check boxes, radio buttons, dates, sliders and grid
  cells cannot be driven yet. When the instruction can only be done with one
  of them, return `kind` `unsupported` and explain in `reason`.
- When the instruction names nothing that is visible on this screen, return
  `kind` `unclear` and explain in `reason`.
- Never leave `button` or `field` empty or `null`. If nothing in the control
  list matches what the instruction describes, return `kind` `unclear` with a
  `reason` instead of guessing a name.
- `confidence` is your confidence (0 to 1) that these actions are what the
  user described.

## Response schema

```json
{
  "kind": "click",
  "button": "Add New",
  "fills": [
    { "column": "Invoice Number", "field": "Invoice No.", "confidence": 0.0 }
  ],
  "reason": "",
  "confidence": 0.0
}
```
