"""Unit tests for inspecting a target window that is minimized."""

from __future__ import annotations

import pytest

from excel_agent.desktop import uia
from excel_agent.desktop.uia import UIAError, _visible_window
from excel_agent.models.screen import Rect, WindowInfo

MINIMISED = Rect(-32000, -32000, -31801, -31966)
VISIBLE = Rect(-9, -9, 1929, 1089)


def _window(rect: Rect) -> WindowInfo:
    return WindowInfo(
        handle=42,
        title="Clients",
        process_name="brave.exe",
        class_name="Chrome_WidgetWin_1",
        rect=rect,
        client_rect=rect,
        dpi=96,
    )


def test_a_minimized_window_is_restored_and_re_read(monkeypatch) -> None:
    shown: list[tuple[int, int]] = []
    monkeypatch.setattr(uia.win32gui, "IsWindow", lambda handle: True)
    monkeypatch.setattr(uia.win32gui, "IsIconic", lambda handle: True)
    monkeypatch.setattr(
        uia.win32gui, "ShowWindow", lambda handle, command: shown.append((handle, command))
    )
    monkeypatch.setattr(uia, "get_window", lambda handle: _window(VISIBLE))
    monkeypatch.setattr(uia.time, "sleep", lambda seconds: None)

    restored = _visible_window(_window(MINIMISED))

    assert restored.rect == VISIBLE
    assert shown == [(42, uia.win32con.SW_RESTORE)]


def test_a_visible_window_is_returned_untouched(monkeypatch) -> None:
    monkeypatch.setattr(uia.win32gui, "IsWindow", lambda handle: True)
    monkeypatch.setattr(uia.win32gui, "IsIconic", lambda handle: False)
    monkeypatch.setattr(
        uia.win32gui,
        "ShowWindow",
        lambda handle, command: pytest.fail("a visible window must not be touched"),
    )

    window = _window(VISIBLE)

    assert _visible_window(window) is window


def test_a_window_that_stays_off_screen_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(uia.win32gui, "IsWindow", lambda handle: True)
    monkeypatch.setattr(uia.win32gui, "IsIconic", lambda handle: True)
    monkeypatch.setattr(uia.win32gui, "ShowWindow", lambda handle, command: None)
    monkeypatch.setattr(uia, "get_window", lambda handle: _window(MINIMISED))
    monkeypatch.setattr(uia.time, "sleep", lambda seconds: None)

    with pytest.raises(UIAError, match="minimized"):
        _visible_window(_window(MINIMISED))


def test_a_dead_window_handle_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(uia.win32gui, "IsWindow", lambda handle: False)

    with pytest.raises(UIAError, match="no longer valid"):
        _visible_window(_window(VISIBLE))
