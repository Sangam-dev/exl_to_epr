"""Unit tests for screen models, window matching, capture, and annotation."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from excel_agent.desktop.capture import Capture, _has_black_padding
from excel_agent.desktop.window import (
    WindowAmbiguousError,
    WindowNotFoundError,
    _process_matches,
    find_window,
)
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo
from excel_agent.utils.images import annotate_candidates


def _window(title: str, process_name: str = "app.exe") -> WindowInfo:
    return WindowInfo(
        handle=1,
        title=title,
        process_name=process_name,
        class_name="TestWindow",
        rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100),
        dpi=96,
    )


def test_rect_geometry_and_edges() -> None:
    rect = Rect(10, 20, 110, 220)

    assert rect.width == 100
    assert rect.height == 200
    assert rect.center == (60, 120)
    assert rect.contains(10, 20)
    assert rect.contains(109, 219)
    assert not rect.contains(110, 120)
    assert not rect.contains(60, 220)


def test_rect_never_reports_negative_size() -> None:
    rect = Rect(100, 100, 40, 40)

    assert rect.width == 0
    assert rect.height == 0


def test_rect_intersects_uses_exclusive_edges() -> None:
    rect = Rect(0, 0, 100, 100)

    assert rect.intersects(Rect(50, 50, 150, 150))
    assert rect.intersects(Rect(99, 99, 200, 200))
    assert not rect.intersects(Rect(100, 0, 200, 100))
    assert not rect.intersects(Rect(0, 100, 100, 200))


def test_process_matches_accepts_name_with_or_without_exe() -> None:
    assert _process_matches("notepad.exe", "notepad")
    assert _process_matches("notepad", "notepad.exe")
    assert _process_matches("Notepad.EXE", "notepad")
    assert not _process_matches("notepad.exe", "calc")
    assert not _process_matches("", "notepad")


def test_find_window_requires_a_filter() -> None:
    with pytest.raises(ValueError, match="required"):
        find_window()


def test_find_window_returns_the_single_match(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _window("Excel Agent Test Form", "powershell.exe")
    monkeypatch.setattr(
        "excel_agent.desktop.window.list_windows",
        lambda: [_window("Other"), target],
    )

    assert find_window("test form") is target


def test_find_window_reports_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "excel_agent.desktop.window.list_windows",
        lambda: [_window("Other")],
    )

    with pytest.raises(WindowNotFoundError, match="no visible window"):
        find_window("missing")


def test_find_window_rejects_ambiguous_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "excel_agent.desktop.window.list_windows",
        lambda: [_window("Test A"), _window("Test B")],
    )

    with pytest.raises(WindowAmbiguousError, match="2 windows matched"):
        find_window("test")


def test_black_padding_detects_dpi_virtualized_capture() -> None:
    image = Image.new("RGB", (200, 100), "black")
    ImageDraw.Draw(image).rectangle([0, 0, 159, 79], fill="white")

    assert _has_black_padding(image)


def test_full_content_is_not_black_padding() -> None:
    assert not _has_black_padding(Image.new("RGB", (200, 100), "white"))


def test_capture_maps_image_pixels_to_screen() -> None:
    window = _window("Test")
    capture = Capture(
        image=Image.new("RGB", (200, 100), "white"),
        origin=(100, 200),
        scale_x=0.5,
        scale_y=2.0,
        window=window,
    )

    assert capture.to_screen(0, 0) == (100, 200)
    assert capture.to_screen(10, 20) == (105, 240)


def test_annotate_candidates_draws_boxes_and_keeps_the_source(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (200, 100), "white")
    candidate = UIACandidate(
        candidate_id="W1",
        name="Save",
        control_type="Button",
        automation_id="btnSave",
        class_name="Button",
        rect=Rect(60, 50, 120, 90),
        value="",
        enabled=True,
    )
    output = tmp_path / "annotated.png"

    annotate_candidates(image, [candidate], (50, 40), output)

    assert output.is_file()
    assert image.getpixel((10, 10)) == (255, 255, 255)
    assert Image.open(output).getpixel((10, 10)) == (255, 0, 0)


def test_annotate_candidates_skips_offscreen_boxes(tmp_path: Path) -> None:
    image = Image.new("RGB", (50, 50), "white")
    candidate = UIACandidate(
        candidate_id="W1",
        name="Hidden",
        control_type="Button",
        automation_id="",
        class_name="Button",
        rect=Rect(500, 500, 520, 520),
        value="",
        enabled=True,
    )
    output = tmp_path / "annotated.png"

    annotate_candidates(image, [candidate], (0, 0), output)

    assert output.is_file()
    assert Image.open(output).getpixel((0, 0)) == (255, 255, 255)
