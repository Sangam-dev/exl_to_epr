"""Global low-level input hooks (Segment 3).

While blocking, external mouse and keyboard events are swallowed. Events
carrying :data:`~excel_agent.desktop.input.AGENT_TAG` are allowed through, as
are the configured stop/pause/resume shortcuts. In passthrough mode (paused)
normal input works, but the shortcuts are still detected.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading

from ..desktop.input import AGENT_TAG
from .hotkeys import ALT_KEYS, Hotkey, find_hotkey

logger = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)

ULONG_PTR = ctypes.c_size_t


class InputLockError(RuntimeError):
    """The input hooks could not be installed or stopped."""


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    _HOOKPROC,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.GetMessageW.restype = ctypes.c_int
_user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def keyboard_decision(
    vk: int,
    is_down: bool,
    extra_info: int,
    tag: int,
    pressed: set[int],
    hotkeys: tuple[Hotkey, ...],
    releases: set[int] | None = None,
) -> tuple[bool, str | None]:
    """Decide one keyboard event.

    Returns ``(allow, action)``:

    * ``allow`` is true for agent-tagged events and for key-ups of a shortcut,
    * ``action`` is the shortcut action for a fresh hotkey press, else ``None``.

    The ``pressed`` set is updated in place so Alt state and auto-repeat are
    tracked without asking the OS. ``releases`` remembers shortcut keys whose
    key-up must reach the system, otherwise a blocked Alt key-up can leave the
    modifier stuck down after resume.
    """
    if extra_info == tag:
        return True, None

    if not is_down:
        pressed.discard(vk)
        if releases is not None and vk in releases:
            releases.discard(vk)
            return True, None
        return False, None

    alt_down = bool(pressed & ALT_KEYS)
    was_down = vk in pressed
    pressed.add(vk)
    if was_down:
        return False, None

    hotkey = find_hotkey(hotkeys, vk, alt_down)
    if hotkey is not None and releases is not None:
        releases.add(vk)
        releases.update(pressed & ALT_KEYS)
    return False, hotkey.action if hotkey is not None else None


class InputLock:
    """Install global low-level hooks on a dedicated thread."""

    def __init__(
        self,
        hotkeys: tuple[Hotkey, ...],
        on_action,
        tag: int = AGENT_TAG,
    ) -> None:
        self._hotkeys = hotkeys
        self._on_action = on_action
        self._tag = tag
        self._blocking = True
        self._pressed: set[int] = set()
        self._releases: set[int] = set()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._keyboard_hook = None
        self._mouse_hook = None

        # Keep the callback objects alive for as long as the hooks exist.
        self._keyboard_proc = _HOOKPROC(self._on_keyboard)
        self._mouse_proc = _HOOKPROC(self._on_mouse)

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def blocking(self) -> bool:
        return self._blocking

    def start(self, blocking: bool = True) -> None:
        """Install the hooks and wait until they are active."""
        if self.active:
            self._blocking = blocking
            return

        self._blocking = blocking
        self._pressed.clear()
        self._releases.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, name="input-lock", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(5.0):
            raise InputLockError("input hook thread did not start")
        if self._error is not None:
            raise InputLockError(str(self._error))
        logger.info("input lock installed (blocking=%s)", blocking)

    def set_blocking(self, blocking: bool) -> None:
        """Switch between blocking and passthrough without removing hooks."""
        self._blocking = bool(blocking)
        logger.info("input lock blocking=%s", self._blocking)

    def stop(self, timeout: float = 5.0) -> None:
        """Remove the hooks and stop the hook thread."""
        self._blocking = False
        self._unhook()

        thread = self._thread
        if thread is not None:
            if self._thread_id:
                _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if threading.current_thread() is not thread:
                thread.join(timeout)
                if thread.is_alive():
                    raise InputLockError("input hook thread did not stop")
            self._thread = None

        self._thread_id = 0
        self._pressed.clear()
        self._releases.clear()
        logger.info("input lock removed")

    def _run(self) -> None:
        try:
            self._thread_id = int(_kernel32.GetCurrentThreadId())
            self._keyboard_hook = _user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._keyboard_proc, None, 0
            )
            if not self._keyboard_hook:
                raise InputLockError(
                    "could not install the keyboard hook "
                    f"(error {ctypes.get_last_error()})"
                )
            self._mouse_hook = _user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_proc, None, 0
            )
            if not self._mouse_hook:
                raise InputLockError(
                    "could not install the mouse hook "
                    f"(error {ctypes.get_last_error()})"
                )
        except Exception as exc:
            self._error = exc
            self._ready.set()
            self._unhook()
            return

        self._ready.set()
        message = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        finally:
            self._unhook()

    def _unhook(self) -> None:
        if self._keyboard_hook:
            _user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._keyboard_hook = None
        if self._mouse_hook:
            _user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    def _call_next(self, n_code: int, w_param: int, l_param: int) -> int:
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_keyboard(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code != HC_ACTION:
            return self._call_next(n_code, w_param, l_param)

        try:
            info = ctypes.cast(
                l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
            ).contents
            is_down = w_param in _DOWN_MESSAGES
            allow, action = keyboard_decision(
                int(info.vkCode),
                is_down,
                int(info.dwExtraInfo),
                self._tag,
                self._pressed,
                self._hotkeys,
                self._releases,
            )
        except Exception:
            # Never trap the user because of an unexpected hook error.
            logger.exception("keyboard hook failed; allowing the event")
            return self._call_next(n_code, w_param, l_param)

        if action is not None:
            try:
                self._on_action(action)
            except Exception:
                logger.exception("hotkey action failed")

        if allow or (action is None and not self._blocking):
            return self._call_next(n_code, w_param, l_param)
        return 1

    def _on_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code != HC_ACTION:
            return self._call_next(n_code, w_param, l_param)

        try:
            info = ctypes.cast(
                l_param, ctypes.POINTER(MSLLHOOKSTRUCT)
            ).contents
            tagged = int(info.dwExtraInfo) == self._tag
        except Exception:
            logger.exception("mouse hook failed; allowing the event")
            return self._call_next(n_code, w_param, l_param)

        if tagged or not self._blocking:
            return self._call_next(n_code, w_param, l_param)
        return 1

    def __enter__(self) -> "InputLock":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
