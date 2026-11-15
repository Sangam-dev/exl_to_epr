"""Screen-point checks shared by the wheel and click paths.

Automation aims at absolute coordinates taken from a UI Automation snapshot.
By the time a coordinate is used, the window may have moved, been covered, been
minimised, or another application may be in front - and Windows delivers a mouse
event to whatever is under the point, not to the window that was meant.

Both the wheel (:mod:`excel_agent.desktop.scroll`) and the click
(:func:`excel_agent.desktop.input.click_checked`) therefore check the point
against the live window before using it, and refuse instead of acting blind.
"""

from __future__ import annotations

from ..models.screen import WindowInfo

# A minimised window reports its rectangle here instead of its real place.
MINIMISED = -30000

try:  # pragma: no cover - non-Windows
    import win32gui
    import win32process
except ImportError:  # pragma: no cover - non-Windows
    win32gui = None  # type: ignore[assignment]
    win32process = None  # type: ignore[assignment]


def process_id(hwnd: int) -> int:
    """Return the process that owns *hwnd*, or 0 when it cannot be read."""
    if win32process is None:
        return 0
    try:
        return int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        return 0


def point_owned_by(window: WindowInfo, x: int, y: int) -> bool:
    """Return True when the point is on the live window and on the app itself.

    False means the point no longer means what it did when the screen was read:
    the window moved, its rectangle is the minimised placeholder, the point lies
    outside it, or another application's window is on top of it.
    """
    if win32gui is None:  # pragma: no cover - non-Windows
        return False
    try:
        if not win32gui.IsWindow(window.handle):
            return False
        left, top, right, bottom = win32gui.GetWindowRect(window.handle)
    except Exception:
        return False

    if left <= MINIMISED or top <= MINIMISED:
        return False
    if not (left <= x < right and top <= y < bottom):
        return False

    try:
        under = win32gui.WindowFromPoint((int(x), int(y)))
    except Exception:
        return False
    if not under:
        return False
    return process_id(under) == process_id(window.handle)
