# Post-save verification prompt (draft - implemented in Segment 6)

Given the pre-save and post-save screenshots, decide whether the record was
actually saved.

Evidence may include: the form cleared, a success message appeared, a new row
appeared in a grid, or the window title changed.

Heuristic: if the same text is still visible at the same field coordinates,
treat the record as **not saved**.

Return JSON:

```json
{ "approved": true, "saved": true, "evidence": [], "issues": [] }
```
