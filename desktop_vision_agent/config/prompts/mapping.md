# Mapping review prompt (draft - implemented in Segment 5)

Given the grounding JSON and the Excel headers, review the proposed
column -> field mapping.

Return JSON with:

- `approved`: boolean,
- `issues`: list of problems (missing required column, duplicate field,
  type mismatch, low confidence),
- `suggested_changes`: optional corrections.

Reject the mapping if any required column is unmapped or a field is used twice.
