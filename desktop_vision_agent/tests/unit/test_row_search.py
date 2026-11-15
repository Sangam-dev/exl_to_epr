"""The downward-only search for a saved row that landed below the fold."""

from __future__ import annotations

from typing import Sequence

import pytest

from excel_agent.agent.row_search import RowSearchUnsupported, find_appended_row
from excel_agent.desktop.uia import UIAInspection
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo

WINDOW = WindowInfo(
    handle=9,
    title="Clients - Brave",
    process_name="brave.exe",
    class_name="Chrome_WidgetWin_1",
    rect=Rect(0, 0, 1200, 800),
    client_rect=Rect(0, 0, 1200, 800),
    dpi=96,
)

ROW_VALUES = {"Client ID": "CL-1001", "Client / Company Name": "Himalayan Traders"}


def _candidate(
    name: str, *, control_type: str = "DataItem", value: str = ""
) -> UIACandidate:
    return UIACandidate(
        candidate_id=f"C{abs(hash((name, control_type))) % 100000}",
        name=name,
        control_type=control_type,
        automation_id="",
        class_name="",
        rect=Rect(0, 0, 10, 10),
        value=value,
        enabled=True,
    )


def _screen(*names: str, add: bool = True) -> UIAInspection:
    """One rendered slice of the page: the visible rows, and the Add button."""
    candidates = [_candidate(name) for name in names]
    if add:
        candidates.append(_candidate("+ Add", control_type="Button"))
    return UIAInspection(window=WINDOW, candidates=tuple(candidates), _controls={})


def _open_form(*names: str) -> UIAInspection:
    """The page with the entry form still open, holding the row's values."""
    candidates = [
        _candidate("Client ID", control_type="Edit", value="CL-1001"),
        _candidate("+ Add", control_type="Button"),
    ]
    candidates.extend(_candidate(name) for name in names)
    return UIAInspection(window=WINDOW, candidates=tuple(candidates), _controls={})


def _reader(*screens: UIAInspection):
    """A cursor over screens: it walks them, then keeps showing the last."""
    remaining = list(screens)

    def read(_window: WindowInfo, **_kwargs) -> UIAInspection | None:
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return read


class _Wheel:
    def __init__(self, result: bool = True) -> None:
        self.calls: list[int] = []
        self.result = result

    def __call__(self, _window: WindowInfo, notches: int) -> bool:
        self.calls.append(notches)
        return self.result


def _search(
    before: UIAInspection | None,
    screens: Sequence[UIAInspection],
    wheel: _Wheel,
    **kwargs,
):
    options = dict(
        read=_reader(*screens[1:]),
        scroll=wheel,
        page_down=None,
        sleep=lambda _seconds: None,
    )
    options.update(kwargs)
    return find_appended_row(
        screens[0],
        window=WINDOW,
        before=before,
        values=ROW_VALUES,
        footprint=["Client ID"],
        **options,
    )


def test_a_row_already_on_screen_is_confirmed_without_scrolling() -> None:
    wheel = _Wheel()
    before = _screen("CL-1000")

    search = _search(before, [_screen("CL-1000", "CL-1001")], wheel)

    assert search.result.outcome == "confirmed"
    assert search.screens == 0
    assert wheel.calls == []


def test_a_row_one_screen_down_is_found_by_scrolling() -> None:
    """Row 15: it was saved, but the list had grown past the fold."""
    wheel = _Wheel()
    before = _screen("CL-1010", "CL-1011", "CL-1012")
    after_save = _screen("CL-1010", "CL-1011", "CL-1012")
    bottom = _screen("CL-1013", "CL-1001")

    search = _search(before, [after_save, bottom], wheel)

    assert search.result.outcome == "confirmed"
    assert search.screens == 1
    assert "1 screen(s) down" in search.result.reason
    assert wheel.calls == [5]


def test_a_row_that_is_not_there_is_reported_as_searched() -> None:
    """The end of the list is reached, and the status says the list was read."""
    wheel = _Wheel()
    before = _screen("CL-1010", "CL-1011")
    after_save = _screen("CL-1010", "CL-1011")
    second = _screen("CL-1012", "CL-1013")

    search = _search(before, [after_save, second], wheel)

    assert search.result.outcome == "unknown"
    assert "to the end of the list" in search.result.reason
    assert search.screens >= 2
    # Two consecutive unchanged screens mean the bottom, so the wheel stops.
    assert len(wheel.calls) == search.screens


def test_an_open_form_is_never_scrolled() -> None:
    """A rejected save leaves the form open; the row it would prove is absent."""
    wheel = _Wheel()
    open_form = _open_form("CL-1010")

    search = find_appended_row(
        open_form,
        window=WINDOW,
        before=None,
        values=ROW_VALUES,
        footprint=["Client ID"],
        read=_reader(open_form),
        scroll=wheel,
        sleep=lambda _seconds: None,
    )

    assert search.result.outcome == "unknown"
    assert "the form still shows" in search.result.reason
    assert wheel.calls == []


def test_a_vanished_add_button_stops_the_search() -> None:
    """If the Add button moves, the page scrolls as a whole: unsupported."""
    wheel = _Wheel()
    before = _screen("CL-1010", "CL-1011")
    scrolled_page = _screen("CL-1011", "CL-1012", add=False)

    search = _search(
        before, [before, scrolled_page], wheel, anchor_labels=["+ Add"]
    )

    assert search.fatal
    assert "scrolls the whole page" in search.fatal
    assert search.screens == 1


def test_the_search_stops_at_max_screens() -> None:
    wheel = _Wheel()
    before = _screen("CL-1000")
    screens = [_screen(f"ROW-{index}") for index in range(10)]

    search = _search(before, screens, wheel, max_screens=3)

    assert search.screens == 3
    assert len(wheel.calls) == 3


def test_a_window_that_cannot_scroll_is_not_wheeled_forever() -> None:
    wheel = _Wheel(result=False)

    search = _search(_screen("CL-1010"), [_screen("CL-1010")], wheel)

    assert search.result.outcome == "unknown"
    assert search.screens == 0
    assert len(wheel.calls) == 1
    # The status says the list was never read, not that the row is missing.
    assert "could not be scrolled" in search.result.reason


def test_a_wheel_that_moves_nothing_falls_back_to_page_down() -> None:
    """A wheel the page never sees must not be taken for the end of the list."""
    wheel = _Wheel()
    before = _screen("CL-1000")
    keys: list[int] = []

    def no_wheel(_window, _notches):
        wheel.calls.append(0)
        return True

    def key_press(_window) -> bool:
        keys.append(1)
        return True

    search = _search(
        before,
        [before, before, _screen("CL-1000", "CL-1001")],
        wheel,
        scroll=no_wheel,
        page_down=key_press,
    )

    assert search.result.outcome == "confirmed"
    assert keys == [1]
    assert "1 screen(s) down" in search.result.reason


def test_a_screen_that_never_moves_says_so() -> None:
    """Nothing moved at all - so the row is not reported as searched for."""
    wheel = _Wheel()
    before = _screen("CL-1010")

    search = _search(before, [before, before], wheel, page_down=lambda _w: False)

    assert search.result.outcome == "unknown"
    assert search.moved is False
    assert search.screens == 2
    assert "did not move when it was scrolled" in search.result.reason
    assert "to the end of the list" not in search.result.reason


# ---------------------------------------------------------------------------
# Wiring: the config decides whether the search runs at all
# ---------------------------------------------------------------------------


def _config(scrolling: dict | None = None):
    from excel_agent.config import AgentConfig

    payload = {
        "app": {"title_contains": "Clients"},
        "excel": {
            "input_path": "in.xlsx",
            "output_path": "out.xlsx",
            "sheet": "Sheet1",
            "id_column": "Client ID",
        },
        "actions": {"save_button": "Save client"},
        "confirmation": {"verifier": "row", "footprint": ["Client ID"]},
    }
    if scrolling is not None:
        payload["scrolling"] = scrolling
    return AgentConfig.model_validate(payload)


def test_scrolling_is_on_by_default_and_can_be_switched_off() -> None:
    assert _config().scrolling.enabled is True
    assert _config({"enabled": False}).scrolling.enabled is False


def test_no_verifier_reads_no_screen_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """``verifier: none`` fills and saves without looking at the screen."""
    from excel_agent.agent import form_steps

    def refuse(*_args, **_kwargs):
        raise AssertionError("no screen check should run when verifier is none")

    monkeypatch.setattr(form_steps, "inspect_window", refuse)
    monkeypatch.setattr(form_steps, "find_appended_row", refuse)

    config = _config()
    config.confirmation.verifier = "none"
    result = form_steps._verify_save(
        config=config,
        confirmation=None,
        window=WINDOW,
        before=None,
        post=None,
        evidence="",
        values=ROW_VALUES,
    )

    assert result.outcome == "confirmed"
    assert "not checked" in result.reason


def test_verify_save_does_not_search_when_scrolling_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excel_agent.agent import form_steps

    def refuse(*_args, **_kwargs):
        raise AssertionError("the row search must not run when scrolling is off")

    monkeypatch.setattr(form_steps, "find_appended_row", refuse)
    monkeypatch.setattr(
        form_steps, "inspect_window", lambda _window, **_kwargs: _screen("CL-1010")
    )

    result = form_steps._verify_save(
        config=_config({"enabled": False}),
        confirmation=None,
        window=WINDOW,
        before=_screen("CL-1010"),
        post=None,
        evidence="",
        values=ROW_VALUES,
    )

    assert result.outcome == "unknown"


def test_verify_save_scrolls_when_scrolling_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excel_agent.agent import form_steps

    top = _screen("CL-1010")
    monkeypatch.setattr(form_steps, "inspect_window", lambda _w, **_k: top)
    monkeypatch.setattr("excel_agent.agent.row_search.scroll_window", lambda *_a: True)
    monkeypatch.setattr(
        "excel_agent.agent.row_search.inspect_window",
        _reader(_screen("CL-1010"), _screen("CL-1001")),
    )

    result = form_steps._verify_save(
        config=_config({"settle_seconds": 0}),
        confirmation=None,
        window=WINDOW,
        before=_screen("CL-1010"),
        post=None,
        evidence="",
        values=ROW_VALUES,
    )

    assert result.outcome == "confirmed"
    assert "screen(s) down" in result.reason


def test_verify_save_raises_when_the_app_scrolls_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excel_agent.agent import form_steps

    top = _screen("CL-1010")
    monkeypatch.setattr(form_steps, "inspect_window", lambda _w, **_k: top)
    monkeypatch.setattr("excel_agent.agent.row_search.scroll_window", lambda *_a: True)
    monkeypatch.setattr(
        "excel_agent.agent.row_search.inspect_window",
        _reader(_screen("CL-1011", add=False), _screen("CL-1011", add=False)),
    )

    with pytest.raises(RowSearchUnsupported):
        form_steps._verify_save(
            config=_config({"settle_seconds": 0}),
            confirmation=None,
            window=WINDOW,
            before=_screen("CL-1010"),
            post=None,
            evidence="",
            values=ROW_VALUES,
            anchor_labels=["+ Add"],
        )
