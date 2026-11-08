"""Low-level mouse/keyboard input tagged as agent input (Segment 3).

Every event carries :data:`AGENT_TAG` in ``dwExtraInfo``. The input lock treats
tagged events as its own and lets them through, while blocking external input.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time

import pyperclip

_user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
ULONG = wintypes.ULONG
DWORD = wintypes.DWORD
WORD = wintypes.WORD
LONG = wintypes.LONG

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

VK_CONTROL = 0x11
VK_A = 0x41
VK_DELETE = 0x2E
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_V = 0x56

# A private value that identifies events injected by this agent. Low-level
# hooks compare against it; nothing else in the system uses this value.
AGENT_TAG = 0x45584C41  # "EXLA"


class InputError(RuntimeError):
    """An input event could not be sent."""


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", DWORD),
        ("wParamL", WORD),
        ("wParamH", WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("type", DWORD),
        ("value", _INPUTUNION),
    ]


_user32.SendInput.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
]
_user32.SendInput.restype = wintypes.UINT
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL


def _key_input(vk: int, scan: int, flags: int) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=AGENT_TAG,
        ),
    )


def _mouse_input(flags: int) -> INPUT:
    return INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=AGENT_TAG,
        ),
    )


def _utf16_units(text: str) -> list[int]:
    """Split text into UTF-16 code units (surrogate pairs for non-BMP)."""
    data = text.encode("utf-16-le")
    return [
        int.from_bytes(data[index : index + 2], "little")
        for index in range(0, len(data), 2)
    ]


def _unicode_inputs(text: str) -> list[INPUT]:
    inputs: list[INPUT] = []
    for unit in _utf16_units(text):
        inputs.append(_key_input(0, unit, KEYEVENTF_UNICODE))
        inputs.append(_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return inputs


def _send(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise InputError(
            f"SendInput sent {sent} of {len(inputs)} events "
            f"(error {ctypes.get_last_error()})"
        )


def type_text(text: str) -> None:
    """Type text as Unicode key events; the target window must have focus."""
    _send(_unicode_inputs(text))


def press_key(vk: int) -> None:
    """Press and release one virtual key."""
    _send([_key_input(vk, 0, 0), _key_input(vk, 0, KEYEVENTF_KEYUP)])


def press_chord(modifier_vk: int, key_vk: int) -> None:
    """Press modifier + key, then release both."""
    _send(
        [
            _key_input(modifier_vk, 0, 0),
            _key_input(key_vk, 0, 0),
            _key_input(key_vk, 0, KEYEVENTF_KEYUP),
            _key_input(modifier_vk, 0, KEYEVENTF_KEYUP),
        ]
    )


def paste_text(text: str) -> None:
    """Put text on the clipboard and send Ctrl+V.

    The clipboard is left with the pasted text; the next paste overwrites it.
    """
    try:
        pyperclip.copy(text)
    except Exception as exc:
        raise InputError(f"could not copy to the clipboard: {exc}") from exc
    # Give the clipboard a moment to settle before the target reads it.
    time.sleep(0.05)
    press_chord(VK_CONTROL, VK_V)


def click(x: int, y: int, button: str = "left") -> None:
    """Move the cursor to a screen point and click it."""
    if not _user32.SetCursorPos(int(x), int(y)):
        raise InputError(f"could not move the cursor to ({x}, {y})")
    if button == "left":
        _send([_mouse_input(MOUSEEVENTF_LEFTDOWN), _mouse_input(MOUSEEVENTF_LEFTUP)])
    elif button == "right":
        _send([_mouse_input(MOUSEEVENTF_RIGHTDOWN), _mouse_input(MOUSEEVENTF_RIGHTUP)])
    else:
        raise ValueError(f"unsupported mouse button: {button!r}")
