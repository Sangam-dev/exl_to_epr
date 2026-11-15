"""Scroll the target window with the wheel, for evidence that is off-screen.

A list that grows downwards puts a freshly saved row below the fold, and a
browser only exposes the rows it is drawing - so the new row is simply not in
the UI Automation tree until the page is scrolled. Turning the wheel is the one
thing every app understands, and it needs no accessibility pattern (browsers
often expose none).

The wheel goes wherever the pointer is, so the pointer is placed on the page
itself and checked to belong to the app: our own status overlay, a sidebar, or
a window that has been minimised since it was found would all swallow it. When
even that fails, :func:`page_down_window` scrolls the focused page with the
keyboard instead.
"""

from __future__ import annotations

import time

from ..models.screen import WindowInfo
from .points import MINIMISED, process_id
from .window import focus_window, get_window

# One wheel notch; Windows reports distance in these units.
_WHEEL_DELTA = 120
# Notches per call: a few hundred pixels, so a step cannot shoot past the row.
_CHUNK = 10
# Chrome draws the scrolled rows a moment after the wheel event.
_SETTLE = 0.35
# A minimised window reports its rectangle here instead of its real place.
_MINIMISED = MINIMISED
# Page Down, for pages that ignore a wheel the pointer cannot be placed on.
_VK_NEXT = 0x22


def scroll_window(
    window: WindowInfo, notches: int, point: tuple[int, int] | None = None
) -> bool:
    """Turn the wheel over *window*; positive scrolls down, negative up.

    Returns ``False`` when the window cannot be scrolled (gone, not
    foreground-able, no part of the page actually on screen, or the platform
    refuses), so callers can stop asking.
    """
    try:
        import win32api
        import win32con
        import win32gui
    except ImportError:  # pragma: no cover - non-Windows
        return False
    if not notches:
        return True
    try:
        if not win32gui.IsWindow(window.handle):
            return False
        # The rectangle the window was found with can be stale - a minimise
        # and restore in between leaves it at the (-32000, -32000) placeholder,
        # which would put the pointer in a corner of the screen. Read it again.
        live = get_window(window.handle)
        if not focus_window(live):
            return False
        target = point or _page_point(live)
        if target is None:
            return False
        x, y = target
        saved = win32api.GetCursorPos()
        win32api.SetCursorPos((x, y))
        try:
            direction = 1 if notches > 0 else -1
            left = abs(int(notches))
            while left > 0:
                step = min(_CHUNK, left)
                win32api.mouse_event(
                    win32con.MOUSEEVENTF_WHEEL,
                    0,
                    0,
                    -direction * step * _WHEEL_DELTA,
                    0,
                )
                left -= step
                if left > 0:
                    time.sleep(0.05)
        finally:
            win32api.SetCursorPos(saved)
        time.sleep(_SETTLE)
        return True
    except Exception:
        return False


def page_down_window(window: WindowInfo) -> bool:
    """Scroll the focused page down one screen with the keyboard.

    Used when a wheel changed nothing: Page Down moves the page that has the
    focus, so it works even when the pointer cannot be put on the content.
    Returns whether the key was sent.
    """
    try:
        from .input import press_key
    except ImportError:  # pragma: no cover - non-Windows
        return False
    try:
        live = get_window(window.handle)
        if not focus_window(live):
            return False
        press_key(_VK_NEXT)
        time.sleep(_SETTLE)
        return True
    except Exception:
        return False


def _page_point(window: WindowInfo) -> tuple[int, int] | None:
    """A point on the page itself, where a wheel reaches the content.

    The middle of the window is not good enough - it can be over the title bar,
    a sidebar, or a panel of our own - so the point is taken from the live
    client area and kept only if the window under it belongs to the app.
    """
    try:
        import win32gui
    except ImportError:  # pragma: no cover - non-Windows
        return None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(window.handle)
        if right - left <= 0 or bottom - top <= 0:
            return None
        if left <= _MINIMISED or top <= _MINIMISED:
            return None
        client = win32gui.GetClientRect(window.handle)
        origin = win32gui.ClientToScreen(window.handle, (0, 0))
    except Exception:
        return None
    width, height = client[2] - client[0], client[3] - client[1]
    if width <= 0 or height <= 0:
        return None
    pid = process_id(window.handle)
    # Down the page rather than at its exact middle: the middle can be a header
    # or a tooltip strip, while the rows are what has to move.
    for fraction in (0.5, 0.6, 0.4, 0.7):
        candidate = (origin[0] + width // 2, origin[1] + int(height * fraction))
        under = win32gui.WindowFromPoint(candidate)
        if under and process_id(under) == pid:
            return candidate
    return None
