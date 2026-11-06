"""Gemini client wrapper (planned: Segment 4).

Responsibilities:
    * create the client from ``GEMINI_API_KEY`` only,
    * bounded retries with backoff,
    * robust JSON extraction from model output,
    * log requests/responses to ``data/ai_logs`` with secrets redacted.
"""

from __future__ import annotations

__all__: list[str] = []
