"""Coordinate conversion and caching (planned: Segment 4).

Responsibilities:
    * convert Gemini's normalised 0-1000 boxes to screen coordinates,
    * validate boxes against the captured window bounds,
    * cache the trusted coordinate map and detect layout drift.
"""

from __future__ import annotations

__all__: list[str] = []
