"""Integration tests for window capture (Segment 9)."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Generator

import pytest
from PIL import Image

from excel_agent.desktop.capture import Capture, CaptureError, capture_window
from excel_agent.desktop.window import WindowNotFoundError, find_window
from excel_agent.models.screen import Rect, WindowInfo


@pytest.fixture(scope="module")
def fixture_window() -> Generator[WindowInfo, None, None]:
    """Launch the test form fixture for live capture testing."""
    proc = subprocess.Popen(
        [sys.executable, "desktop_vision_agent/scripts/test_form.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    window = None
    for _ in range(25):
        time.sleep(0.2)
        try:
            window = find_window("Excel Agent Test Form")
            break
        except Exception:
            pass

    if window is None:
        proc.terminate()
        pytest.skip("Test form window could not be opened in current environment.")

    try:
        yield window
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def test_capture_invalid_window_raises() -> None:
    invalid_win = WindowInfo(
        handle=99999999,
        title="Non-existent Window",
        process_name="none.exe",
        class_name="None",
        rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100),
        dpi=96,
    )
    with pytest.raises((CaptureError, WindowNotFoundError)):
        capture_window(invalid_win)


def test_capture_to_screen_mapping() -> None:
    dummy_win = WindowInfo(
        handle=1,
        title="Dummy",
        process_name="dummy.exe",
        class_name="Dummy",
        rect=Rect(100, 200, 500, 600),
        client_rect=Rect(100, 200, 500, 600),
        dpi=96,
    )
    cap = Capture(
        image=Image.new("RGB", (400, 400), color="white"),
        origin=(100, 200),
        scale_x=1.0,
        scale_y=1.0,
        window=dummy_win,
    )
    assert cap.to_screen(50, 50) == (150, 250)
    assert cap.to_screen(0, 0) == (100, 200)


def test_capture_live_fixture_window(fixture_window: WindowInfo) -> None:
    capture = capture_window(fixture_window)
    assert capture is not None
    assert isinstance(capture.image, Image.Image)
    assert capture.image.width > 0
    assert capture.image.height > 0
    assert capture.window.handle == fixture_window.handle
    # Converted center should fall within the window bounds
    cx, cy = capture.to_screen(capture.image.width // 2, capture.image.height // 2)
    assert fixture_window.rect.contains(cx, cy)
