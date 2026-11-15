# Read a region of the target window

You are given one cropped screenshot of a small region of a desktop
application. Transcribe the visible text exactly.

Rules:

- Return only the text you can actually see in the image.
- Preserve the original spelling, punctuation, and spacing as closely as
  possible.
- Do not guess or invent text that is not visible.
- If the region contains no readable text, return an empty string.
- Do not describe the image or add commentary.

Return ONE JSON object and nothing else. No markdown, no code fences.

```
{"text": "<the visible text>"}
```
