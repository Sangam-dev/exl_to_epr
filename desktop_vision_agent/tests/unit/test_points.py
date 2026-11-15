"""The point check that stops clicks landing in another window (V1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from excel_agent.desktop import input as input_module
from excel_agent.desktop import points
from excel_agent.desktop.input import InputError, click_checked
from excel_agent.models.screen import Rect, WindowInfo

WINDOW = WindowInfo(
    handle=100,
    title="Clients - Brave",
    process_name="brave.exe",
    class_name="Chrome_WidgetWin_1",
    rect=Rect(0, 0, 800, 600),
    client_rect=Rect(0, 0, 800, 560),
    dpi=96,
)

APP_PID = 4242
OTHER_PID = 999


def _win32(
    *,
    alive: bool = True,
    rect: tuple[int, int, int, int] = (0, 0, 800, 600),
    under: int | None = 555,
    under_pid: int = APP_PID,
) -> SimpleNamespace:
    windows = {100: APP_PID, 555: under_pid, 777: OTHER_PID}
    return SimpleNamespace(
        IsWindow=lambda _hwnd: alive,
        GetWindowRect=lambda _hwnd: rect,
        WindowFromPoint=lambda _point: under,
        _pids=windows,
    )


def _process(pid_map: dict[int, int]) -> SimpleNamespace:
    return SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, pid_map.get(hwnd, 0))
    )


def _install(monkeypatch, gui: SimpleNamespace) -> None:
    monkeypatch.setattr(points, "win32gui", gui)
    monkeypatch.setattr(points, "win32process", _process(gui._pids))


def test_a_point_on_the_app_is_owned(monkeypatch) -> None:
    _install(monkeypatch, _win32())

    assert points.point_owned_by(WINDOW, 400, 300) is True


def test_a_point_over_another_application_is_not_owned(monkeypatch) -> None:
    _install(monkeypatch, _win32(under=777))

    assert points.point_owned_by(WINDOW, 400, 300) is False


def test_a_point_outside_the_window_is_not_owned(monkeypatch) -> None:
    _install(monkeypatch, _win32())

    assert points.point_owned_by(WINDOW, 900, 300) is False


def test_the_minimised_placeholder_rect_is_never_owned(monkeypatch) -> None:
    _install(monkeypatch, _win32(rect=(-32000, -32000, -31801, -31966)))

    assert points.point_owned_by(WINDOW, -31900, -31980) is False


def test_a_window_that_has_been_closed_is_not_owned(monkeypatch) -> None:
    _install(monkeypatch, _win32(alive=False))

    assert points.point_owned_by(WINDOW, 400, 300) is False


def test_nothing_is_sent_when_the_point_is_over_another_window(monkeypatch) -> None:
    clicks: list[tuple[int, int]] = []
    monkeypatch.setattr(input_module, "click", lambda x, y, _b="left": clicks.append((x, y)))
    monkeypatch.setattr(input_module, "point_owned_by", lambda *_a, **_k: False)

    with pytest.raises(InputError) as excinfo:
        click_checked(400, 300, WINDOW)

    assert "nothing was clicked" in str(excinfo.value)
    assert clicks == []


def test_the_click_is_sent_once_when_the_point_is_owned(monkeypatch) -> None:
    clicks: list[tuple[int, int]] = []
    monkeypatch.setattr(input_module, "click", lambda x, y, _b="left": clicks.append((x, y)))
    monkeypatch.setattr(input_module, "point_owned_by", lambda *_a, **_k: True)

    click_checked(400, 300, WINDOW)

    assert clicks == [(400, 300)]


def test_a_background_window_refuses_the_click_when_foreground_is_required(
    monkeypatch,
) -> None:
    clicks: list[tuple[int, int]] = []
    monkeypatch.setattr(input_module, "click", lambda x, y, _b="left": clicks.append((x, y)))
    monkeypatch.setattr(input_module, "point_owned_by", lambda *_a, **_k: True)
    monkeypatch.setattr(input_module, "is_foreground", lambda _hwnd: False)

    with pytest.raises(InputError) as excinfo:
        click_checked(400, 300, WINDOW, require_foreground=True)

    assert "not in the foreground" in str(excinfo.value)
    assert clicks == []


def test_the_foreground_is_not_required_unless_asked(monkeypatch) -> None:
    clicks: list[tuple[int, int]] = []
    monkeypatch.setattr(input_module, "click", lambda x, y, _b="left": clicks.append((x, y)))
    monkeypatch.setattr(input_module, "point_owned_by", lambda *_a, **_k: True)
    monkeypatch.setattr(input_module, "is_foreground", lambda _hwnd: False)

    click_checked(400, 300, WINDOW)

    assert clicks == [(400, 300)]
