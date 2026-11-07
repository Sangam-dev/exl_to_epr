"""Hotkey parsing and matching for Alt+T / Alt+P / Alt+R (Segment 3)."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SafetyConfig

VK_MENU = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
ALT_KEYS = frozenset({VK_MENU, VK_LMENU, VK_RMENU})

_SUPPORTED_MODIFIERS = {"alt"}


class HotkeyError(ValueError):
    """A configured hotkey is not supported."""


@dataclass(frozen=True)
class Hotkey:
    """One configured shortcut, for example ``Alt+T``."""

    action: str
    label: str
    vk: int
    alt: bool = True


def parse_hotkey(action: str, value: str) -> Hotkey:
    """Parse ``alt+<letter-or-digit>`` into a :class:`Hotkey`."""
    parts = [part.strip().lower() for part in value.split("+")]
    if len(parts) != 2 or not all(parts):
        raise HotkeyError(f"hotkey {value!r} must look like 'alt+t'")

    modifier, key = parts
    if modifier not in _SUPPORTED_MODIFIERS:
        raise HotkeyError(
            f"hotkey {value!r} uses unsupported modifier {modifier!r}; only alt is supported"
        )
    if len(key) != 1 or not (key.isalpha() or key.isdigit()):
        raise HotkeyError(
            f"hotkey {value!r} must end in one letter or digit, for example 'alt+t'"
        )
    if not key.isascii():
        raise HotkeyError(f"hotkey {value!r} must use an ASCII letter or digit")

    return Hotkey(
        action=action,
        label=f"Alt+{key.upper()}",
        vk=ord(key.upper()),
    )


def hotkeys_from_config(config: SafetyConfig) -> tuple[Hotkey, ...]:
    """Return the three configured hotkeys, rejecting duplicates."""
    hotkeys = (
        parse_hotkey("stop", config.stop_hotkey),
        parse_hotkey("pause", config.pause_hotkey),
        parse_hotkey("resume", config.resume_hotkey),
    )
    labels = [hotkey.label for hotkey in hotkeys]
    if len(labels) != len(set(labels)):
        raise HotkeyError(
            "stop, pause, and resume must use different hotkeys: "
            + ", ".join(labels)
        )
    return hotkeys


def find_hotkey(
    hotkeys: tuple[Hotkey, ...], vk: int, alt_down: bool
) -> Hotkey | None:
    """Return the hotkey matching this key press, if any."""
    for hotkey in hotkeys:
        if hotkey.vk == vk and hotkey.alt == alt_down:
            return hotkey
    return None
