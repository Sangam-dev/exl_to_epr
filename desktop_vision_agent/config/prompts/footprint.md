# Which value identifies the saved row on this screen?

You are given one screenshot of a desktop application taken straight after a
record was saved. The record was filled from a single spreadsheet row.

The values that were typed, by column name:

{{CANDIDATES}}

Pick the ONE value that the screen now shows as part of the saved record - a
row in a table, a detail panel, or a "record created" line. Prefer a value that
belongs to this record alone (an ID, code, or reference number) over a field
many records can share, such as a city or a status word.

Rules:

- Choose only from the column names listed above.
- Choose a value you can actually see in the image.
- If no typed value is visible, return an empty string for the column.

Return ONE JSON object and nothing else. No markdown, no code fences.

```
{"column": "<column name from the list, or an empty string>"}
```
