"""Target-window discovery, focus, and geometry (Segment 2)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os

import win32api
import win32con
import win32gui
import win32process

from ..config import AppConfig
from ..models.screen import Rect, WindowInfo

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_dpi_awareness_set = False


class WindowError(RuntimeError):
    """Base error for window discovery and focus."""


class WindowNotFoundError(WindowError):
    """No visible top-level window matched the configuration."""


class WindowAmbiguousError(WindowError):
    """More than one window matched; the configuration must be more specific."""


class WindowFocusError(WindowError):
    """The target window could not be brought to the foreground."""


def enable_dpi_awareness() -> None:
    """Make screenshots and UIA coordinates agree on scaled displays."""
    global _dpi_awareness_set
    if _dpi_awareness_set or os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass
    _dpi_awareness_set = True


def _process_name(process_id: int) -> str:
    if not process_id:
        return ""
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
    finally:
        _kernel32.CloseHandle(handle)
    return ""


def _window_rect(hwnd: int) -> Rect:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return Rect(int(left), int(top), int(right), int(bottom))


def _client_rect(hwnd: int) -> Rect:
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    origin_x, origin_y = win32gui.ClientToScreen(hwnd, (0, 0))
    return Rect(
        int(origin_x),
        int(origin_y),
        int(origin_x + right),
        int(origin_y + bottom),
    )


def _dpi(hwnd: int) -> int:
    try:
        return int(_user32.GetDpiForWindow(hwnd)) or 96
    except Exception:
        return 96


def get_window(hwnd: int) -> WindowInfo:
    """Return a fresh snapshot of one window handle."""
    if not win32gui.IsWindow(hwnd):
        raise WindowNotFoundError(f"window handle 0x{hwnd:X} is no longer valid")
    _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
    return WindowInfo(
        handle=hwnd,
        title=win32gui.GetWindowText(hwnd).strip(),
        process_name=_process_name(process_id),
        class_name=win32gui.GetClassName(hwnd),
        rect=_window_rect(hwnd),
        client_rect=_client_rect(hwnd),
        dpi=_dpi(hwnd),
    )


def list_windows() -> list[WindowInfo]:
    """List visible top-level windows that have a title."""
    windows: list[WindowInfo] = []

    def callback(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if not win32gui.GetWindowText(hwnd).strip():
            return True
        try:
            info = get_window(hwnd)
        except Exception:
            return True
        if info.rect.width <= 0 or info.rect.height <= 0:
            return True
        windows.append(info)
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def _process_matches(actual: str, wanted: str) -> bool:
    actual = actual.strip().lower()
    wanted = wanted.strip().lower()
    if not actual or not wanted:
        return False
    return (
        actual == wanted
        or actual == f"{wanted}.exe"
        or f"{actual}.exe" == wanted
    )


def find_window(title_contains: str = "", process_name: str | None = None) -> WindowInfo:
    """Find exactly one visible window matching the configured filters."""
    if not title_contains and not process_name:
        raise ValueError("title_contains or process_name is required")

    matches = [
        window
        for window in list_windows()
        if (
            (not title_contains or title_contains.lower() in window.title.lower())
            and (not process_name or _process_matches(window.process_name, process_name))
        )
    ]

    if not matches:
        raise WindowNotFoundError(
            f"no visible window matched title={title_contains!r} "
            f"process={process_name!r}"
        )
    if len(matches) > 1:
        described = ", ".join(
            f"{window.title!r} ({window.process_name or 'unknown'})"
            for window in matches[:10]
        )
        raise WindowAmbiguousError(
            f"{len(matches)} windows matched; make app.title_contains or "
            f"app.process_name more specific: {described}"
        )
    return matches[0]


def is_foreground(hwnd: int) -> bool:
    return int(win32gui.GetForegroundWindow()) == int(hwnd)


def focus_window(window: WindowInfo) -> bool:
    """Restore and focus the target window; return whether it succeeded."""
    hwnd = window.handle
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    if is_foreground(hwnd):
        return True

    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    if is_foreground(hwnd):
        return True

    # Windows blocks SetForegroundWindow from background processes. Attaching
    # to the foreground/target input queues is the standard workaround.
    foreground = win32gui.GetForegroundWindow()
    current_thread = win32api.GetCurrentThreadId()
    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
    foreground_thread = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    )

    attached: list[int] = []
    try:
        for thread in {foreground_thread, target_thread}:
            if thread and thread != current_thread:
                if _user32.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetFocus(hwnd)
        except Exception:
            pass
    finally:
        for thread in attached:
            _user32.AttachThreadInput(current_thread, thread, False)

    return is_foreground(hwnd)


def attach_window(config: AppConfig) -> WindowInfo:
    """Find the target window and, if required, bring it to the foreground."""
    window = find_window(config.title_contains, config.process_name)

    if win32gui.IsIconic(window.handle):
        focus_window(window)
        window = get_window(window.handle)

    if config.require_foreground and not is_foreground(window.handle):
        focus_window(window)
        window = get_window(window.handle)

    if config.require_foreground and not is_foreground(window.handle):
        raise WindowFocusError(
            "target window is not in the foreground; click it once and retry"
        )

    return window


# Set DPI awareness before any window geometry is read so screenshots, UIA
# rectangles, and mouse coordinates all use the same physical pixel space.
enable_dpi_awareness()
