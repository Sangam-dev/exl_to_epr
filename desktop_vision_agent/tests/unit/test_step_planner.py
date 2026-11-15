"""Unit tests for turning one plain-words instruction into click/fill actions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from excel_agent.ai.step_planner import (
    StepPlan,
    StepPlanError,
    StepUnsupported,
    build_step_prompt,
    match_label,
    parse_step_plan,
    plan_step,
)
from excel_agent.config import GeminiConfig
from excel_agent.desktop.capture import Capture
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo

HEADERS = ["Invoice Number", "Customer", "Amount", "Notes"]


def _candidate(
    candidate_id: str,
    name: str,
    control_type: str = "Button",
    rect: Rect | None = None,
) -> UIACandidate:
    return UIACandidate(
        candidate_id=candidate_id,
        name=name,
        control_type=control_type,
        automation_id="",
        class_name="",
        rect=rect or Rect(10, 10, 110, 34),
        value="",
        enabled=True,
    )


CANDIDATES = (
    _candidate("W1", "Add New"),
    _candidate("W2", "Save"),
    _candidate("W3", "Invoice No.", "Edit"),
    _candidate("W4", "Customer Name", "Edit"),
    _candidate("W5", "Total", "Edit"),
    _candidate("W6", "Country", "ComboBox"),
)


def _capture() -> Capture:
    window = WindowInfo(
        handle=1,
        title="App",
        process_name="app.exe",
        class_name="AppWindow",
        rect=Rect(0, 0, 800, 600),
        client_rect=Rect(0, 0, 800, 600),
        dpi=96,
    )
    return Capture(
        image=Image.new("RGB", (800, 600), "white"),
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=window,
    )


# ---------------------------------------------------------------------------
# Label lookup
# ---------------------------------------------------------------------------


def test_match_label_ignores_case_and_extra_spaces() -> None:
    matched = match_label(CANDIDATES, "  add   NEW ", frozenset({"Button"}))
    assert matched is not None
    assert matched.name == "Add New"


def test_match_label_respects_the_control_type() -> None:
    assert match_label(CANDIDATES, "Total", frozenset({"Button"})) is None
    assert match_label(CANDIDATES, "Total", frozenset({"Edit"})) is not None


def test_match_label_returns_none_for_an_unknown_name() -> None:
    assert match_label(CANDIDATES, "Delete", frozenset({"Button"})) is None


# ---------------------------------------------------------------------------
# Parsing a click
# ---------------------------------------------------------------------------


def test_a_click_resolves_to_the_live_button_name() -> None:
    plan = parse_step_plan(
        {"kind": "click", "button": "add new", "confidence": 0.9},
        candidates=CANDIDATES,
        headers=HEADERS,
    )

    assert plan.kind == "click"
    assert plan.label == "Add New"
    assert plan.confidence == 0.9
    assert plan.fills == ()


def test_an_unknown_button_names_the_nearby_ones() -> None:
    with pytest.raises(StepPlanError) as caught:
        parse_step_plan(
            {"kind": "click", "button": "Add"},
            candidates=CANDIDATES,
            headers=HEADERS,
        )

    message = str(caught.value)
    assert "no button on this screen is named 'Add'" in message
    assert "Add New" in message


def test_a_click_without_a_button_name_is_rejected() -> None:
    with pytest.raises(StepPlanError):
        parse_step_plan(
            {"kind": "click", "button": "  "},
            candidates=CANDIDATES,
            headers=HEADERS,
        )


# ---------------------------------------------------------------------------
# Parsing a fill
# ---------------------------------------------------------------------------


def test_a_partial_fill_only_lists_the_matched_columns() -> None:
    plan = parse_step_plan(
        {
            "kind": "fill",
            "fills": [
                {"column": "Invoice Number", "field": "Invoice No.", "confidence": 0.8},
                {"column": "Amount", "field": "Total", "confidence": 0.7},
            ],
        },
        candidates=CANDIDATES,
        headers=HEADERS,
    )

    assert plan.kind == "fill"
    assert plan.columns == ("Invoice Number", "Amount")
    assert [fill.label for fill in plan.fills] == ["Invoice No.", "Total"]


def test_a_fill_without_its_own_confidence_uses_the_step_confidence() -> None:
    plan = parse_step_plan(
        {
            "kind": "fill",
            "fills": [{"column": "Amount", "field": "Total"}],
            "confidence": 0.75,
        },
        candidates=CANDIDATES,
        headers=HEADERS,
    )

    assert plan.fills[0].confidence == 0.75


def test_a_column_that_is_not_a_header_is_rejected() -> None:
    with pytest.raises(StepPlanError) as caught:
        parse_step_plan(
            {
                "kind": "fill",
                "fills": [{"column": "Invoice", "field": "Invoice No."}],
            },
            candidates=CANDIDATES,
            headers=HEADERS,
        )

    message = str(caught.value)
    assert "'Invoice' is not an Excel column" in message
    assert "Invoice Number" in message


def test_a_field_that_is_not_on_screen_is_rejected() -> None:
    with pytest.raises(StepPlanError) as caught:
        parse_step_plan(
            {"kind": "fill", "fills": [{"column": "Notes", "field": "Notes"}]},
            candidates=CANDIDATES,
            headers=HEADERS,
        )

    assert "no text field on this screen is named 'Notes'" in str(caught.value)


def test_a_dropdown_is_not_treated_as_a_text_field() -> None:
    with pytest.raises(StepPlanError):
        parse_step_plan(
            {"kind": "fill", "fills": [{"column": "Notes", "field": "Country"}]},
            candidates=CANDIDATES,
            headers=HEADERS,
        )


def test_the_last_entry_wins_for_a_repeated_column() -> None:
    plan = parse_step_plan(
        {
            "kind": "fill",
            "fills": [
                {"column": "Amount", "field": "Total"},
                {"column": "Amount", "field": "Customer Name"},
            ],
        },
        candidates=CANDIDATES,
        headers=HEADERS,
    )

    assert len(plan.fills) == 1
    assert plan.fills[0].label == "Customer Name"


def test_the_same_field_is_only_typed_once() -> None:
    plan = parse_step_plan(
        {
            "kind": "fill",
            "fills": [
                {"column": "Amount", "field": "Total"},
                {"column": "Notes", "field": "total"},
            ],
        },
        candidates=CANDIDATES,
        headers=HEADERS,
    )

    assert plan.columns == ("Amount",)


def test_an_empty_fill_list_is_rejected() -> None:
    with pytest.raises(StepPlanError):
        parse_step_plan({"kind": "fill", "fills": []}, candidates=CANDIDATES, headers=HEADERS)


# ---------------------------------------------------------------------------
# Instructions this mode cannot do
# ---------------------------------------------------------------------------


def test_a_dropdown_instruction_is_refused_with_its_reason() -> None:
    with pytest.raises(StepUnsupported) as caught:
        parse_step_plan(
            {"kind": "unsupported", "reason": "Country is a dropdown"},
            candidates=CANDIDATES,
            headers=HEADERS,
        )

    assert "Country is a dropdown" in str(caught.value)


def test_an_unclear_instruction_keeps_the_model_reason() -> None:
    with pytest.raises(StepUnsupported) as caught:
        parse_step_plan(
            {"kind": "unclear", "reason": "no such thing on screen"},
            candidates=CANDIDATES,
            headers=HEADERS,
        )

    assert "no such thing on screen" in str(caught.value)


def test_an_unknown_kind_is_a_plan_error() -> None:
    with pytest.raises(StepPlanError) as caught:
        parse_step_plan({"kind": "scroll"}, candidates=CANDIDATES, headers=HEADERS)

    assert "does not match the schema" in str(caught.value)


# ---------------------------------------------------------------------------
# Prompt and one client call
# ---------------------------------------------------------------------------


def test_the_prompt_carries_the_instruction_columns_and_controls() -> None:
    prompt = build_step_prompt(
        "fill up all the form",
        HEADERS,
        CANDIDATES,
        800,
        600,
    )

    assert "fill up all the form" in prompt
    assert "- Invoice Number" in prompt
    assert "name=Invoice No." in prompt
    assert "(none)" in prompt  # no correction yet


def test_the_correction_reaches_the_prompt() -> None:
    prompt = build_step_prompt(
        "fill the amount",
        HEADERS,
        CANDIDATES,
        800,
        600,
        correction="the amount goes in the Total box, not Customer Name",
    )

    assert "the amount goes in the Total box" in prompt


class _FakeClient:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, image=None, **_kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(data=self._data)


def test_plan_step_sends_one_prompt_and_parses_the_answer() -> None:
    client = _FakeClient(
        {"kind": "click", "button": "Add New", "confidence": 0.95}
    )

    plan = plan_step(
        client,
        _capture(),
        CANDIDATES,
        HEADERS,
        "click Add New",
        GeminiConfig(),
    )

    assert plan == StepPlan(kind="click", label="Add New", confidence=0.95)
    assert len(client.prompts) == 1
    assert "click Add New" in client.prompts[0]


def test_a_null_button_is_a_plan_error_not_a_crash() -> None:
    with pytest.raises(StepPlanError, match="did not name a button"):
        parse_step_plan(
            {"kind": "click", "button": None, "confidence": None},
            candidates=CANDIDATES,
            headers=HEADERS,
        )


def test_a_click_on_a_screen_without_buttons_says_so() -> None:
    candidates = (_candidate("W1", "Invoice No.", "Edit"),)

    with pytest.raises(StepPlanError, match="exposes no buttons"):
        parse_step_plan(
            {"kind": "click", "button": "Add"},
            candidates=candidates,
            headers=HEADERS,
        )


def test_null_fill_entries_are_reported_not_crashed() -> None:
    with pytest.raises(StepPlanError, match="no Excel column"):
        parse_step_plan(
            {"kind": "fill", "fills": [{"column": None, "field": None}]},
            candidates=CANDIDATES,
            headers=HEADERS,
        )


def test_a_fill_on_a_screen_without_text_fields_says_so() -> None:
    candidates = (_candidate("W1", "Add New"),)

    with pytest.raises(StepPlanError, match="no text fields"):
        parse_step_plan(
            {"kind": "fill", "fills": [{"column": "Notes", "field": "Notes"}]},
            candidates=candidates,
            headers=HEADERS,
        )
