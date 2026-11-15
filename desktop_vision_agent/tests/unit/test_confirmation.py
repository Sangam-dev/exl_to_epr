"""Unit tests for the pluggable save-confirmation strategy (Segment 10)."""

from __future__ import annotations

import pytest
from PIL import Image

from excel_agent.agent.confirmation import (
    CONFIRMED,
    REJECTED,
    UNKNOWN,
    ConfirmationError,
    ConfirmationResult,
    TextConfirmation,
)
from excel_agent.ai.client import GeminiError
from excel_agent.config import ConfirmationConfig, NormalizedRegion


class FakeReader:
    """Deterministic text reader used instead of OCR or Gemini."""

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.images: list[Image.Image] = []

    def read_text(self, image: Image.Image) -> str:
        self.images.append(image)
        if self.error is not None:
            raise self.error
        return self.text


def _image(width: int = 100, height: int = 50) -> Image.Image:
    return Image.new("RGB", (width, height), "white")


def _config(
    *,
    success_pattern: str = "",
    error_patterns: list[str] | None = None,
    success_region: tuple[float, float, float, float] | None = None,
    error_region: tuple[float, float, float, float] | None = None,
) -> ConfirmationConfig:
    def region(values):
        if values is None:
            return None
        return NormalizedRegion(x1=values[0], y1=values[1], x2=values[2], y2=values[3])

    return ConfirmationConfig(
        success_pattern=success_pattern,
        error_patterns=error_patterns or [],
        success_region=region(success_region),
        error_region=region(error_region),
    )


def test_no_configured_pattern_is_unknown_and_never_reads() -> None:
    reader = FakeReader("Status: saved")
    strategy = TextConfirmation(_config(), reader)

    result = strategy.confirm(_image(), weak_hint="the filled fields changed")

    assert result.outcome == UNKNOWN
    assert reader.images == []
    assert "no success or error pattern" in result.reason


def test_success_text_confirms() -> None:
    reader = FakeReader("Status: saved 10:31:02")
    strategy = TextConfirmation(
        _config(success_pattern=r"saved\s+\d{2}:\d{2}:\d{2}"), reader
    )

    result = strategy.confirm(_image())

    assert result.outcome == CONFIRMED
    assert len(reader.images) == 1
    assert "saved" in result.reason.casefold()


def test_error_text_rejects_even_when_success_text_is_present() -> None:
    reader = FakeReader("Status: saved; error: duplicate invoice")
    strategy = TextConfirmation(
        _config(success_pattern="saved", error_patterns=[r"\berror\b", "duplicate"]),
        reader,
    )

    result = strategy.confirm(_image())

    assert result.outcome == REJECTED
    assert "duplicate" in result.reason.casefold()


def test_missing_success_text_is_unknown() -> None:
    reader = FakeReader("Status: ready")
    strategy = TextConfirmation(_config(success_pattern="saved"), reader)

    result = strategy.confirm(_image())

    assert result.outcome == UNKNOWN
    assert "did not contain" in result.reason


def test_pixel_change_can_never_confirm_by_itself() -> None:
    reader = FakeReader("Status: ready")
    strategy = TextConfirmation(_config(success_pattern="saved"), reader)

    result = strategy.confirm(
        _image(), weak_hint="the filled fields changed"
    )

    assert result.outcome == UNKNOWN
    assert "weak hint" in " ".join(result.evidence).casefold()


def test_reader_failure_is_unknown_not_an_exception() -> None:
    reader = FakeReader(error=GeminiError("quota exceeded"))
    strategy = TextConfirmation(_config(success_pattern="saved"), reader)

    result = strategy.confirm(_image())

    assert result.outcome == UNKNOWN
    assert "reader failed" in result.reason
    assert "quota" in result.reason


def test_success_region_is_cropped_before_reading() -> None:
    reader = FakeReader("saved")
    strategy = TextConfirmation(
        _config(
            success_pattern="saved",
            success_region=(0, 0, 500, 500),
        ),
        reader,
    )

    strategy.confirm(_image(100, 50))

    assert reader.images[0].size == (50, 25)


def test_a_shared_region_is_only_read_once() -> None:
    reader = FakeReader("Status: ready")
    strategy = TextConfirmation(
        _config(
            success_pattern="saved",
            error_patterns=["error"],
            success_region=(0, 0, 1000, 1000),
            error_region=(0, 0, 1000, 1000),
        ),
        reader,
    )

    strategy.confirm(_image())

    assert len(reader.images) == 1


def test_invalid_pattern_is_rejected_at_config_time() -> None:
    with pytest.raises(ValueError, match="regular expression"):
        _config(success_pattern="[")


def test_result_defaults_are_explicit() -> None:
    result = ConfirmationResult(outcome=UNKNOWN)

    assert result.reason == ""
    assert result.evidence == ()
    assert result.source == ""


def test_confirmation_error_is_a_runtime_error() -> None:
    with pytest.raises(ConfirmationError):
        raise ConfirmationError("bad response")
