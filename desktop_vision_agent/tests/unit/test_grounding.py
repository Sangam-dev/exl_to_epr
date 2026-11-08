"""Unit tests for Gemini grounding (Segment 4)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from PIL import Image

from excel_agent.ai.client import (
    AIResponse,
    AIResponseError,
    GeminiClient,
    GeminiError,
    extract_json,
)
from excel_agent.ai.grounding import (
    GroundingError,
    ground_window,
    parse_grounding,
)
from excel_agent.ai.prompts import (
    PromptError,
    build_grounding_prompt,
    render_prompt,
)
from excel_agent.config import GeminiConfig
from excel_agent.desktop.capture import Capture
from excel_agent.desktop.coordinates import (
    CoordinateError,
    normalized_to_image,
    normalized_to_screen,
    rect_within,
)
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo
from excel_agent.utils.images import prepare_image


def _window() -> WindowInfo:
    return WindowInfo(
        handle=1,
        title="Invoice Form",
        process_name="test.exe",
        class_name="Form",
        rect=Rect(100, 200, 900, 800),
        client_rect=Rect(100, 200, 900, 800),
        dpi=96,
    )


def _capture() -> Capture:
    return Capture(
        image=Image.new("RGB", (800, 600), "white"),
        origin=(100, 200),
        scale_x=1.0,
        scale_y=1.0,
        window=_window(),
    )


def _candidate(
    candidate_id: str = "W1",
    rect: Rect = Rect(180, 260, 480, 290),
) -> UIACandidate:
    return UIACandidate(
        candidate_id=candidate_id,
        name="Invoice Number",
        control_type="Edit",
        automation_id="txtInvoice",
        class_name="Edit",
        rect=rect,
        value="",
        enabled=True,
    )


def _candidates() -> list[UIACandidate]:
    return [
        _candidate(),
        _candidate("W2", Rect(580, 680, 660, 740)),
    ]


def _payload() -> dict:
    return {
        "screen_description": "Invoice form",
        "form_detected": True,
        "fields": [
            {
                "field_id": "f1",
                "label": "Invoice Number",
                "type": "text",
                "normalized_bbox": {
                    "x1": 100,
                    "y1": 100,
                    "x2": 475,
                    "y2": 150,
                },
                "uia_candidate_id": "W1",
                "current_value": "",
                "confidence": 0.9,
            }
        ],
        "buttons": [
            {
                "button_id": "b1",
                "label": "Save",
                "role": "save",
                "normalized_bbox": {
                    "x1": 600,
                    "y1": 800,
                    "x2": 700,
                    "y2": 900,
                },
                "uia_candidate_id": "W2",
                "confidence": 0.9,
            }
        ],
        "column_mapping": [
            {
                "column": "Invoice Number",
                "field_id": "f1",
                "confidence": 0.95,
                "reason": "same label",
            }
        ],
        "unmapped_columns": ["Notes"],
        "unmapped_fields": [],
        "warnings": [],
        "layout_confidence": 0.9,
    }


def _parse(
    data: dict,
    *,
    candidates: list[UIACandidate] | None = None,
    headers: list[str] | None = None,
    config: GeminiConfig | None = None,
):
    return parse_grounding(
        data,
        candidates=_candidates() if candidates is None else candidates,
        headers=["Invoice Number", "Notes"] if headers is None else headers,
        capture=_capture(),
        config=GeminiConfig() if config is None else config,
    )


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------


def test_normalized_to_image_scales_both_axes() -> None:
    assert normalized_to_image(0, 0, 1000, 1000, 800, 600) == Rect(0, 0, 800, 600)
    assert normalized_to_image(100, 100, 475, 150, 800, 600) == Rect(80, 60, 380, 90)


def test_normalized_to_screen_uses_the_window_origin() -> None:
    assert normalized_to_screen(0, 0, 1000, 1000, _capture()) == Rect(
        100, 200, 900, 800
    )
    assert normalized_to_screen(100, 100, 475, 150, _capture()) == Rect(
        180, 260, 480, 290
    )


@pytest.mark.parametrize(
    "box",
    [(-1, 0, 100, 100), (0, 0, 1001, 100), (500, 0, 400, 100), (0, 500, 100, 400)],
)
def test_normalized_box_rejects_out_of_range_or_reversed_values(box) -> None:
    with pytest.raises(CoordinateError):
        normalized_to_image(*box, 800, 600)


def test_rect_within_allows_one_pixel_of_rounding() -> None:
    bounds = Rect(100, 200, 900, 800)

    assert rect_within(Rect(100, 200, 900, 800), bounds)
    assert rect_within(Rect(99, 199, 901, 801), bounds)
    assert not rect_within(Rect(50, 200, 900, 800), bounds)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the result: {"a": 1} — done.',
    ],
)
def test_extract_json_accepts_common_shapes(text: str) -> None:
    assert extract_json(text) == {"a": 1}


def test_extract_json_rejects_empty_response() -> None:
    with pytest.raises(AIResponseError, match="empty"):
        extract_json("   ")


def test_extract_json_rejects_a_json_array() -> None:
    with pytest.raises(AIResponseError, match="JSON object"):
        extract_json("[1, 2]")


def test_extract_json_rejects_text_without_an_object() -> None:
    with pytest.raises(AIResponseError, match="no JSON object"):
        extract_json("I could not read the screen.")


# ---------------------------------------------------------------------------
# Grounding validation
# ---------------------------------------------------------------------------


def test_parse_grounding_resolves_candidates_and_mapping() -> None:
    result = _parse(_payload())

    assert result.fields["f1"].rect == Rect(180, 260, 480, 290)
    assert result.fields["f1"].type == "text"
    assert result.buttons["b1"].rect == Rect(580, 680, 660, 740)
    assert result.buttons["b1"].type == "save"
    assert result.column_mapping[0].column == "Invoice Number"
    assert result.unmapped_columns == ("Notes",)
    assert result.unmapped_fields == ()
    assert result.layout_confidence == 0.9


def test_parse_grounding_uses_the_visual_box_without_a_candidate() -> None:
    data = _payload()
    data["fields"][0]["uia_candidate_id"] = None
    data["fields"][0]["normalized_bbox"] = {
        "x1": 50,
        "y1": 50,
        "x2": 100,
        "y2": 100,
    }

    result = _parse(data)

    assert result.fields["f1"].rect == Rect(140, 230, 180, 260)


def test_parse_grounding_rejects_an_unknown_candidate() -> None:
    data = _payload()
    data["fields"][0]["uia_candidate_id"] = "W99"

    with pytest.raises(GroundingError, match="unknown UIA candidate"):
        _parse(data)


def test_parse_grounding_rejects_a_duplicate_column() -> None:
    data = _payload()
    data["column_mapping"] = [
        {"column": "Invoice Number", "field_id": "f1"},
        {"column": "Invoice Number", "field_id": "f1"},
    ]

    with pytest.raises(GroundingError, match="mapped more than once"):
        _parse(data)


def test_parse_grounding_rejects_an_unknown_column() -> None:
    data = _payload()
    data["column_mapping"][0]["column"] = "Missing Column"

    with pytest.raises(GroundingError, match="not an Excel header"):
        _parse(data)


def test_parse_grounding_rejects_an_unknown_field_id() -> None:
    data = _payload()
    data["column_mapping"][0]["field_id"] = "f9"

    with pytest.raises(GroundingError, match="not a detected field"):
        _parse(data)


def test_parse_grounding_rejects_a_duplicate_field_id() -> None:
    data = _payload()
    data["fields"].append(copy.deepcopy(data["fields"][0]))

    with pytest.raises(GroundingError, match="duplicate field_id"):
        _parse(data)


def test_parse_grounding_aborts_when_no_form_is_detected() -> None:
    data = _payload()
    data["form_detected"] = False

    with pytest.raises(GroundingError, match="no data-entry form"):
        _parse(data)


def test_parse_grounding_rejects_an_invalid_box() -> None:
    data = _payload()
    data["fields"][0]["normalized_bbox"] = {
        "x1": 500,
        "y1": 100,
        "x2": 400,
        "y2": 150,
    }

    with pytest.raises(GroundingError, match="does not match the schema"):
        _parse(data)


def test_parse_grounding_rejects_an_unsupported_field_type() -> None:
    data = _payload()
    data["fields"][0]["type"] = "date"

    with pytest.raises(GroundingError, match="does not match the schema"):
        _parse(data)


def test_parse_grounding_warns_on_low_layout_confidence() -> None:
    result = _parse(
        _payload(),
        config=GeminiConfig(min_layout_confidence=0.95),
    )

    assert any("below the threshold" in warning for warning in result.warnings)


def test_parse_grounding_warns_when_unmapped_columns_disagree() -> None:
    data = _payload()
    data["unmapped_columns"] = []

    result = _parse(data)

    assert any("unmapped_columns did not match" in w for w in result.warnings)
    assert result.unmapped_columns == ("Notes",)


def test_field_for_column_returns_the_resolved_element() -> None:
    result = _parse(_payload())

    assert result.field_for_column("Invoice Number") is result.fields["f1"]
    assert result.field_for_column("Notes") is None


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_render_prompt_rejects_missing_values() -> None:
    with pytest.raises(PromptError, match="MISSING"):
        render_prompt("hello {{MISSING}}", {})


def test_build_grounding_prompt_includes_headers_and_candidates() -> None:
    prompt = build_grounding_prompt(
        ["Invoice Number", "Notes"],
        _candidates(),
        800,
        600,
    )

    assert "- Invoice Number" in prompt
    assert "- Notes" in prompt
    assert "W1" in prompt
    assert "800x600" in prompt
    # JSON braces in the schema must survive rendering.
    assert '"normalized_bbox"' in prompt


def test_prepare_image_leaves_small_images_alone() -> None:
    image = Image.new("RGB", (400, 300), "white")

    assert prepare_image(image, 1600) is image


def test_prepare_image_scales_down_the_longest_side() -> None:
    image = Image.new("RGB", (4000, 2000), "white")

    resized = prepare_image(image, 1600)

    assert resized.size == (1600, 800)


# ---------------------------------------------------------------------------
# Client behaviour
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, results: list) -> None:
        self.models = FakeModels(results)


def test_client_retries_then_parses(tmp_path: Path) -> None:
    fake = FakeClient([RuntimeError("boom"), '{"ok": true}'])
    delays: list[float] = []
    client = GeminiClient(
        GeminiConfig(max_retries=1),
        api_key="test",
        client=fake,
        log_dir=tmp_path,
        sleep=delays.append,
    )

    response = client.generate_json("hello")

    assert response.data == {"ok": True}
    assert response.attempts == 2
    assert client.calls == 2
    assert fake.models.calls == 2
    assert delays == [1]


def test_client_raises_after_the_retry_budget(tmp_path: Path) -> None:
    fake = FakeClient([RuntimeError("boom"), RuntimeError("boom")])
    client = GeminiClient(
        GeminiConfig(max_retries=1),
        api_key="test",
        client=fake,
        log_dir=tmp_path,
        sleep=lambda _: None,
    )

    with pytest.raises(GeminiError, match="after 2 attempt"):
        client.generate_json("hello")


def test_client_respects_the_call_budget(tmp_path: Path) -> None:
    client = GeminiClient(
        GeminiConfig(max_ai_calls=0),
        api_key="test",
        client=FakeClient([]),
        log_dir=tmp_path,
    )

    with pytest.raises(GeminiError, match="budget"):
        client.generate_json("hello")


def test_client_logs_requests_and_redacts_secrets(tmp_path: Path) -> None:
    secret = "AIza" + "a" * 30
    fake = FakeClient(['{"ok": true}'])
    client = GeminiClient(
        GeminiConfig(),
        api_key="test",
        client=fake,
        log_dir=tmp_path,
    )

    response = client.generate_json(f"api_key={secret}")

    assert response.log_path is not None
    logged = response.log_path.read_text(encoding="utf-8")
    assert secret not in logged
    assert "<REDACTED>" in logged


class CapturingModels:
    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.contents: list[list] = []

    def generate_content(self, **kwargs):
        self.contents.append(kwargs["contents"])
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class CapturingClient:
    def __init__(self, results: list) -> None:
        self.models = CapturingModels(results)


def test_client_sends_multiple_images_in_order(tmp_path: Path) -> None:
    fake = CapturingClient(['{"ok": true}'])
    client = GeminiClient(
        GeminiConfig(),
        api_key="test",
        client=fake,
        log_dir=tmp_path,
    )

    response = client.generate_json_images(
        "compare these",
        [
            Image.new("RGB", (40, 30), "white"),
            Image.new("RGB", (50, 20), "black"),
        ],
    )

    assert response.data == {"ok": True}
    contents = fake.models.contents[0]
    assert len(contents) == 3
    assert contents[-1] == "compare these"
    logged = response.log_path.read_text(encoding="utf-8")
    assert '"width": 40' in logged
    assert '"width": 50' in logged


def test_client_beats_before_every_attempt(tmp_path: Path) -> None:
    fake = FakeClient([RuntimeError("boom"), '{"ok": true}'])
    attempts: list[int] = []
    client = GeminiClient(
        GeminiConfig(max_retries=1),
        api_key="test",
        client=fake,
        log_dir=tmp_path,
        sleep=lambda _: None,
    )

    client.generate_json("hello", on_attempt=attempts.append)

    assert attempts == [1, 2]


class StubGroundingClient:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.prompt = ""
        self.image_size: tuple[int, int] = (0, 0)

    def generate_json(self, prompt: str, image=None) -> AIResponse:
        self.prompt = prompt
        self.image_size = image.size if image is not None else (0, 0)
        return AIResponse(
            data=self.data,
            text="{}",
            attempts=1,
            log_path=None,
        )


def test_ground_window_prepares_the_image_and_prompt() -> None:
    stub = StubGroundingClient(_payload())

    result = ground_window(
        stub,
        _capture(),
        _candidates(),
        ["Invoice Number", "Notes"],
        GeminiConfig(max_image_dimension=400),
    )

    assert stub.image_size == (400, 300)
    assert "W1" in stub.prompt
    assert result.image_size == (400, 300)
    assert result.ai is not None
    assert result.fields["f1"].rect == Rect(180, 260, 480, 290)
