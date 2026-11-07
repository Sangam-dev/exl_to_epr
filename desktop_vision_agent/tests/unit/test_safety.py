"""Unit tests for the safety layer (Segment 3)."""

from __future__ import annotations

import threading
import time

import pytest

from excel_agent.config import SafetyConfig
from excel_agent.desktop.input import (
    AGENT_TAG,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    _key_input,
    _unicode_inputs,
    _utf16_units,
)
from excel_agent.safety.controller import SafetyController, SafetyState
from excel_agent.safety.hotkeys import (
    VK_MENU,
    HotkeyError,
    hotkeys_from_config,
    parse_hotkey,
)
from excel_agent.safety.input_lock import keyboard_decision
from excel_agent.safety.watchdog import Watchdog

HOTKEYS = hotkeys_from_config(SafetyConfig())


def test_parse_hotkey_normalises_label_and_key() -> None:
    hotkey = parse_hotkey("stop", "alt+t")

    assert hotkey.action == "stop"
    assert hotkey.label == "Alt+T"
    assert hotkey.vk == ord("T")
    assert hotkey.alt is True


@pytest.mark.parametrize(
    "value", ["ctrl+t", "alt+tt", "alt+", "t", "alt+space", "alt+é"]
)
def test_parse_hotkey_rejects_unsupported_values(value: str) -> None:
    with pytest.raises(HotkeyError):
        parse_hotkey("stop", value)


def test_hotkeys_from_config_returns_all_three_actions() -> None:
    actions = [hotkey.action for hotkey in HOTKEYS]

    assert actions == ["stop", "pause", "resume"]
    assert [hotkey.label for hotkey in HOTKEYS] == ["Alt+T", "Alt+P", "Alt+R"]


def test_hotkeys_from_config_rejects_duplicates() -> None:
    config = SafetyConfig.model_construct(
        stop_hotkey="alt+t",
        pause_hotkey="alt+t",
        resume_hotkey="alt+r",
    )

    with pytest.raises(HotkeyError, match="different hotkeys"):
        hotkeys_from_config(config)


def test_tagged_key_is_allowed_and_not_a_hotkey() -> None:
    pressed: set[int] = set()

    assert keyboard_decision(
        ord("T"), True, AGENT_TAG, AGENT_TAG, pressed, HOTKEYS
    ) == (True, None)


def test_external_key_is_blocked() -> None:
    pressed: set[int] = set()

    assert keyboard_decision(
        ord("X"), True, 0, AGENT_TAG, pressed, HOTKEYS
    ) == (False, None)


def test_alt_t_fires_stop_once_even_on_auto_repeat() -> None:
    pressed: set[int] = set()

    assert keyboard_decision(VK_MENU, True, 0, AGENT_TAG, pressed, HOTKEYS) == (
        False,
        None,
    )
    assert keyboard_decision(ord("T"), True, 0, AGENT_TAG, pressed, HOTKEYS) == (
        False,
        "stop",
    )
    assert keyboard_decision(ord("T"), True, 0, AGENT_TAG, pressed, HOTKEYS) == (
        False,
        None,
    )


def test_alt_p_and_alt_r_fire_pause_and_resume() -> None:
    pressed: set[int] = set()

    keyboard_decision(VK_MENU, True, 0, AGENT_TAG, pressed, HOTKEYS)
    assert keyboard_decision(ord("P"), True, 0, AGENT_TAG, pressed, HOTKEYS) == (
        False,
        "pause",
    )

    pressed.clear()
    keyboard_decision(VK_MENU, True, 0, AGENT_TAG, pressed, HOTKEYS)
    assert keyboard_decision(ord("R"), True, 0, AGENT_TAG, pressed, HOTKEYS) == (
        False,
        "resume",
    )


def test_plain_t_is_not_a_hotkey() -> None:
    pressed: set[int] = set()

    assert keyboard_decision(
        ord("T"), True, 0, AGENT_TAG, pressed, HOTKEYS
    ) == (False, None)


def test_key_up_releases_the_pressed_key() -> None:
    pressed = {ord("T")}

    assert keyboard_decision(
        ord("T"), False, 0, AGENT_TAG, pressed, HOTKEYS
    ) == (False, None)
    assert ord("T") not in pressed


def test_hotkey_key_ups_pass_through_so_modifiers_do_not_stick() -> None:
    pressed: set[int] = set()
    releases: set[int] = set()

    keyboard_decision(VK_MENU, True, 0, AGENT_TAG, pressed, HOTKEYS, releases)
    assert keyboard_decision(
        ord("R"), True, 0, AGENT_TAG, pressed, HOTKEYS, releases
    ) == (False, "resume")

    assert keyboard_decision(
        ord("R"), False, 0, AGENT_TAG, pressed, HOTKEYS, releases
    ) == (True, None)
    assert keyboard_decision(
        VK_MENU, False, 0, AGENT_TAG, pressed, HOTKEYS, releases
    ) == (True, None)
    assert releases == set()


def test_utf16_units_handles_ascii_bmp_and_emoji() -> None:
    assert _utf16_units("AB") == [ord("A"), ord("B")]
    assert _utf16_units("é") == [0x00E9]
    assert _utf16_units("😀") == [0xD83D, 0xDE00]


def test_unicode_inputs_are_tagged_agent_events() -> None:
    inputs = _unicode_inputs("A")

    assert len(inputs) == 2
    assert all(item.ki.dwExtraInfo == AGENT_TAG for item in inputs)
    assert inputs[0].ki.dwFlags == KEYEVENTF_UNICODE
    assert inputs[1].ki.dwFlags == KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    assert inputs[0].ki.wScan == ord("A")
    assert inputs[0].ki.wVk == 0


def test_key_input_carries_the_agent_tag() -> None:
    item = _key_input(ord("A"), 0, 0)

    assert item.ki.wVk == ord("A")
    assert item.ki.dwExtraInfo == AGENT_TAG


class FakeLock:
    def __init__(self) -> None:
        self.active = False
        self.blocking = False
        self.starts = 0
        self.stops = 0

    def start(self, blocking: bool = True) -> None:
        self.active = True
        self.blocking = blocking
        self.starts += 1

    def set_blocking(self, blocking: bool) -> None:
        self.blocking = blocking

    def stop(self) -> None:
        self.active = False
        self.blocking = False
        self.stops += 1


class FakeOverlay:
    def __init__(self) -> None:
        self.states: list[object] = []

    def start(self) -> None:
        self.states.append("start")

    def update(self, state: str, detail: str = "") -> None:
        self.states.append((state, detail))

    def stop(self) -> None:
        self.states.append("stop")


class FakeWatchdog:
    def __init__(self) -> None:
        self.beats = 0
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def beat(self) -> None:
        self.beats += 1

    def stop(self) -> None:
        self.stops += 1


def _controller() -> tuple[SafetyController, FakeLock, FakeOverlay, FakeWatchdog]:
    lock = FakeLock()
    overlay = FakeOverlay()
    watchdog = FakeWatchdog()
    controller = SafetyController(
        SafetyConfig(), lock=lock, overlay=overlay, watchdog=watchdog
    )
    return controller, lock, overlay, watchdog


def test_controller_start_pause_resume_stop() -> None:
    controller, lock, _overlay, watchdog = _controller()

    controller.start()
    assert controller.running
    assert lock.active and lock.blocking
    assert watchdog.starts == 1

    controller.pause()
    assert controller.state is SafetyState.PAUSED
    assert not lock.blocking
    assert lock.active

    controller.resume()
    assert controller.running
    assert lock.blocking

    controller.stop(reason="test")
    assert controller.stopped
    assert controller.reason == "test"
    assert not lock.active
    assert watchdog.stops == 1

    controller.close()


def test_check_returns_false_after_stop() -> None:
    controller, _lock, _overlay, _watchdog = _controller()

    controller.start()
    controller.stop()

    assert controller.check() is False
    controller.close()


def test_check_blocks_while_paused_and_wakes_on_resume() -> None:
    controller, _lock, _overlay, _watchdog = _controller()
    controller.start()
    controller.pause()

    results: list[bool] = []
    worker = threading.Thread(target=lambda: results.append(controller.check()))
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive()

    controller.resume()
    worker.join(timeout=2.0)

    assert results == [True]
    controller.close()


class StubController:
    def __init__(self) -> None:
        self.running = True
        self.stops: list[str] = []

    def stop(self, reason: str = "") -> None:
        self.stops.append(reason)
        self.running = False


def test_watchdog_releases_when_heartbeat_stops() -> None:
    stub = StubController()
    watchdog = Watchdog(stub, timeout_seconds=0.05, interval_seconds=0.01)

    watchdog.start()
    time.sleep(0.25)
    watchdog.stop()

    assert stub.stops
    assert "watchdog" in stub.stops[0]


def test_watchdog_does_not_fire_while_beating() -> None:
    stub = StubController()
    watchdog = Watchdog(stub, timeout_seconds=0.2, interval_seconds=0.01)

    watchdog.start()
    for _ in range(4):
        time.sleep(0.03)
        watchdog.beat()
    watchdog.stop()

    assert stub.stops == []
