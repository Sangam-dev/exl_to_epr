"""Keep our own windows above other always-on-top windows.

Windows keeps every topmost window in one band. An app that is itself "always
on top" - the browser we automate, say - moves to the front of that band each
time it is activated, so a window that only set the topmost flag once ends up
underneath it. Re-asserting the position with ``SetWindowPos`` and
``SWP_NOACTIVATE`` keeps us in front without taking the focus away from the
app that is being automated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Often enough to win the band back after a click activates the target app,
# rare enough to cost nothing.
INTERVAL_MS = 400

_FLAGS = (
    "SWP_NOMOVE",
    "SWP_NOSIZE",
    "SWP_NOACTIVATE",
)


def raise_above_topmost(widget: object) -> bool:
    """Move a tkinter toplevel to the front of the topmost band.

    Returns ``False`` when the platform or the widget cannot do it, so callers
    can stay silent about it.
    """
    try:
        import win32con
        import win32gui

        handle = widget.winfo_id()  # type: ignore[attr-defined]
        top = win32gui.GetAncestor(handle, win32con.GA_ROOT) or handle
        flags = 0
        for name in _FLAGS:
            flags |= getattr(win32con, name)
        win32gui.SetWindowPos(top, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        return True
    except Exception:
        logger.debug("could not raise a window above the topmost band", exc_info=True)
        return False


def keep_above_topmost(widget: object, interval_ms: int = INTERVAL_MS) -> bool:
    """Re-assert a tkinter window's topmost place for as long as it lives.

    Returns ``True`` when the repeating tick was scheduled.
    """
    if not raise_above_topmost(widget):
        return False

    def tick() -> None:
        raise_above_topmost(widget)
        try:
            widget.after(interval_ms, tick)  # type: ignore[attr-defined]
        except Exception:  # the window was destroyed while we waited
            logger.debug("stopped keeping a window on top", exc_info=True)

    try:
        widget.after(interval_ms, tick)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("could not schedule keeping a window on top", exc_info=True)
        return False
    return True
