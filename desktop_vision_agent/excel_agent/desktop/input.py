"""Low-level mouse/keyboard input (planned: Segment 3).

Responsibilities:
    * SendInput for mouse and Unicode keyboard input,
    * clipboard paste fallback for long or non-ASCII text,
    * tag agent events with a private ``dwExtraInfo`` value so the input lock
      can distinguish them from user input.
"""

from __future__ import annotations

__all__: list[str] = []
