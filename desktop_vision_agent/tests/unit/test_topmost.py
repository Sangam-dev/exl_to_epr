"""Unit tests for keeping our own windows above other always-on-top windows."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import win32con
import win32gui

from excel_agent.desktop.topmost import keep_above_topmost, raise_above_topmost


def test_the_window_is_pushed_into_the_topmost_band_without_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(win32gui, "GetAncestor", lambda handle, _flag: handle + 1)
    monkeypatch.setattr(win32gui, "SetWindowPos", lambda *args: calls.append(args))
    widget = MagicMock()
    widget.winfo_id.return_value = 0x1234

    assert raise_above_topmost(widget) is True

    handle, insert_after, _x, _y, _width, _height, flags = calls[0]
    # The toplevel, not the client window tkinter hands out.
    assert handle == 0x1235
    assert insert_after == win32con.HWND_TOPMOST
    # Re-raising must not take the focus away from the app being automated.
    assert flags & win32con.SWP_NOACTIVATE
    assert flags & win32con.SWP_NOMOVE
    assert flags & win32con.SWP_NOSIZE


def test_a_widget_that_cannot_report_its_handle_is_left_alone() -> None:
    assert raise_above_topmost(object()) is False


def test_the_topmost_place_is_re_asserted_while_the_window_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised: list[object] = []
    scheduled: list[tuple[int, object]] = []
    widget = MagicMock()
    widget.after.side_effect = lambda interval, callback: scheduled.append(
        (interval, callback)
    )
    monkeypatch.setattr(
        "excel_agent.desktop.topmost.raise_above_topmost",
        lambda target: raised.append(target) or True,
    )

    assert keep_above_topmost(widget, interval_ms=250) is True
    assert raised == [widget]
    assert scheduled[0][0] == 250

    scheduled.pop(0)[1]()
    assert raised == [widget, widget]
    assert len(scheduled) == 1


def test_no_tick_is_scheduled_when_the_window_cannot_be_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "excel_agent.desktop.topmost.raise_above_topmost", lambda _target: False
    )
    widget = MagicMock()

    assert keep_above_topmost(widget) is False
    assert not widget.after.called
