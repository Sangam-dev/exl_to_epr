"""Unit tests for pre-save and post-save verification (Segment 6)."""

from __future__ import annotations

import pytest
from PIL import Image

from excel_agent.ai.client import AIResponse
from excel_agent.ai.mapping import MappingEntry
from excel_agent.ai.verification import (
    build_post_save_prompt,
    build_pre_save_prompt,
    check_post_save,
    check_pre_save,
    parse_post_save,
    parse_pre_save,
)
from excel_agent.config import GeminiConfig
from excel_agent.desktop.capture import Capture
from excel_agent.models.screen import Rect, WindowInfo
from excel_agent.models.verification import VerificationError


def _entries() -> list[MappingEntry]:
    return [
        MappingEntry(
            column="Invoice Number",
            field_id="f1",
            label="Invoice Number",
            type="text",
            rect=(10, 20, 110, 40),
            confidence=1.0,
        ),
        MappingEntry(
            column="Amount",
            field_id="f2",
            label="Amount",
            type="number",
            rect=(10, 60, 110, 80),
            confidence=1.0,
        ),
    ]


def _values() -> dict[str, str]:
    return {"Invoice Number": "INV-0001", "Amount": "12.5"}


def _pre_payload() -> dict:
    return {
        "approved": True,
        "confidence": 0.9,
        "field_checks": [
            {
                "field_id": "f1",
                "column": "Invoice Number",
                "expected": "INV-0001",
                "observed": "INV-0001",
                "ok": True,
                "note": "",
            },
            {
                "field_id": "f2",
                "column": "Amount",
                "expected": "12.5",
                "observed": "12.5",
                "ok": True,
                "note": "",
            },
        ],
        "issues": [],
    }


def _post_payload() -> dict:
    return {
        "approved": True,
        "saved": True,
        "confidence": 0.9,
        "form_cleared": True,
        "evidence": ["the fields were cleared"],
        "issues": [],
    }


def _parse_pre(data: dict):
    return parse_pre_save(data, entries=_entries(), values=_values())


def _parse_post(data: dict):
    return parse_post_save(data)


# ---------------------------------------------------------------------------
# Pre-save
# ---------------------------------------------------------------------------


def test_parse_pre_save_approves_a_clean_response() -> None:
    verdict = _parse_pre(_pre_payload())

    assert verdict.approved
    assert verdict.issues == ()
    assert len(verdict.response.field_checks) == 2


def test_parse_pre_save_rejects_a_failed_field_check() -> None:
    data = _pre_payload()
    data["field_checks"][1]["ok"] = False
    data["field_checks"][1]["observed"] = "12.6"

    verdict = _parse_pre(data)

    assert not verdict.approved
    assert any("expected '12.5'" in issue for issue in verdict.issues)


def test_parse_pre_save_rejects_a_missing_field_check() -> None:
    data = _pre_payload()
    data["field_checks"] = data["field_checks"][:1]

    verdict = _parse_pre(data)

    assert not verdict.approved
    assert any("did not check" in issue for issue in verdict.issues)


def test_parse_pre_save_rejects_approval_with_a_failed_check() -> None:
    data = _pre_payload()
    data["field_checks"][0]["ok"] = False

    verdict = _parse_pre(data)

    assert not verdict.approved
    assert any("approved the form even though" in issue for issue in verdict.issues)


def test_parse_pre_save_rejects_a_wrong_echoed_expected_value() -> None:
    data = _pre_payload()
    data["field_checks"][0]["expected"] = "INV-9999"

    verdict = _parse_pre(data)

    assert not verdict.approved
    assert any("the row value is 'INV-0001'" in issue for issue in verdict.issues)


def test_parse_pre_save_accepts_a_number_with_thousands_separator() -> None:
    data = _pre_payload()
    data["field_checks"][1]["expected"] = "12.50"
    data["field_checks"][1]["observed"] = "12.50"

    verdict = _parse_pre(data)

    assert verdict.approved


def test_parse_pre_save_rejects_an_unknown_field() -> None:
    data = _pre_payload()
    data["field_checks"][0]["field_id"] = "f9"
    data["field_checks"][0]["column"] = "Other"

    with pytest.raises(VerificationError, match="not in the mapping"):
        _parse_pre(data)


def test_parse_pre_save_rejects_a_duplicate_field() -> None:
    data = _pre_payload()
    data["field_checks"][1] = dict(data["field_checks"][0])

    with pytest.raises(VerificationError, match="twice"):
        _parse_pre(data)


def test_parse_pre_save_rejects_an_invalid_schema() -> None:
    data = _pre_payload()
    del data["approved"]

    with pytest.raises(VerificationError, match="does not match the schema"):
        _parse_pre(data)


def test_parse_pre_save_reports_model_issues() -> None:
    data = _pre_payload()
    data["issues"] = ["the form looks unusual"]

    verdict = _parse_pre(data)

    assert not verdict.approved
    assert "the form looks unusual" in verdict.issues


# ---------------------------------------------------------------------------
# Post-save
# ---------------------------------------------------------------------------


def test_parse_post_save_approves_a_saved_response() -> None:
    verdict = _parse_post(_post_payload())

    assert verdict.approved
    assert verdict.saved
    assert verdict.issues == ()


def test_parse_post_save_rejects_approval_without_a_save() -> None:
    data = _post_payload()
    data["saved"] = False

    verdict = _parse_post(data)

    assert not verdict.approved
    assert any("reported as not saved" in issue for issue in verdict.issues)


def test_parse_post_save_rejects_a_save_that_was_not_approved() -> None:
    data = _post_payload()
    data["approved"] = False

    verdict = _parse_post(data)

    assert not verdict.approved
    assert verdict.saved
    assert any("did not approve it" in issue for issue in verdict.issues)


def test_parse_post_save_rejects_issues() -> None:
    data = _post_payload()
    data["issues"] = ["the record is still visible"]

    verdict = _parse_post(data)

    assert not verdict.approved
    assert "the record is still visible" in verdict.issues


def test_parse_post_save_rejects_an_invalid_schema() -> None:
    data = _post_payload()
    del data["saved"]

    with pytest.raises(VerificationError, match="does not match the schema"):
        _parse_post(data)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_build_pre_save_prompt_includes_fields_and_size() -> None:
    prompt = build_pre_save_prompt(_entries(), _values(), 800, 600)

    assert "Invoice Number" in prompt
    assert "INV-0001" in prompt
    assert "12.5" in prompt
    assert "800x600" in prompt
    assert '"field_checks"' in prompt


def test_build_post_save_prompt_includes_both_sizes() -> None:
    prompt = build_post_save_prompt(
        _entries(), _values(), (800, 600), (800, 600)
    )

    assert "Before: 800x600" in prompt
    assert "After: 800x600" in prompt
    assert "INV-0001" in prompt
    assert '"form_cleared"' in prompt


# ---------------------------------------------------------------------------
# Gemini wrappers
# ---------------------------------------------------------------------------


class StubClient:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.prompt = ""
        self.images: list[Image.Image] = []
        self.attempts: list[int] = []

    def generate_json(self, prompt, image=None, *, on_attempt=None):
        self.prompt = prompt
        self.images = [image] if image is not None else []
        if on_attempt is not None:
            on_attempt(1)
        return AIResponse(self.data, "{}", 1, None)

    def generate_json_images(self, prompt, images=(), *, on_attempt=None):
        self.prompt = prompt
        self.images = list(images)
        if on_attempt is not None:
            on_attempt(1)
        return AIResponse(self.data, "{}", 1, None)


def _capture(width: int = 800, height: int = 600) -> Capture:
    window = WindowInfo(
        handle=1,
        title="Form",
        process_name="test.exe",
        class_name="Form",
        rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        dpi=96,
    )
    return Capture(
        image=Image.new("RGB", (width, height), "white"),
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=window,
    )


def test_check_pre_save_prepares_the_image_and_prompt() -> None:
    stub = StubClient(_pre_payload())

    verdict = check_pre_save(
        stub,
        _capture(2000, 1200),
        _entries(),
        _values(),
        GeminiConfig(max_image_dimension=800),
    )

    assert verdict.approved
    assert stub.images[0].size == (800, 480)
    assert "800x480" in stub.prompt


def test_check_post_save_sends_two_images() -> None:
    stub = StubClient(_post_payload())

    verdict = check_post_save(
        stub,
        _capture(),
        _capture(),
        _entries(),
        _values(),
        GeminiConfig(),
    )

    assert verdict.approved
    assert len(stub.images) == 2
    assert "Before: 800x600" in stub.prompt
