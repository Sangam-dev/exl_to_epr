"""Screenshot + UIA + Excel headers -> validated field map (Segment 4).

The model returns a strict JSON schema. This module parses it, resolves every
element to a screen rectangle (preferring the UIA rectangle when the model
named a candidate), and rejects responses that violate the mapping rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..config import GeminiConfig
from ..desktop.capture import Capture
from ..desktop.coordinates import (
    CoordinateError,
    normalized_to_screen,
    rect_within,
)
from ..models.screen import Rect, UIACandidate
from ..utils.images import prepare_image
from .client import AIResponse, GeminiClient
from .prompts import build_grounding_prompt


class GroundingError(RuntimeError):
    """The model response is unusable or violates the mapping rules."""


class NormalizedBox(BaseModel):
    """A ``0-1000`` box relative to the screenshot."""

    model_config = ConfigDict(extra="ignore")

    x1: float = Field(ge=0, le=1000)
    y1: float = Field(ge=0, le=1000)
    x2: float = Field(ge=0, le=1000)
    y2: float = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def _ordered(self) -> "NormalizedBox":
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError(
                f"normalized_bbox must have x1 < x2 and y1 < y2, got "
                f"({self.x1}, {self.y1}, {self.x2}, {self.y2})"
            )
        return self


class GroundedField(BaseModel):
    """One text or number input found in the form."""

    model_config = ConfigDict(extra="ignore")

    field_id: str = Field(min_length=1)
    label: str = ""
    type: Literal["text", "number"]
    normalized_bbox: NormalizedBox
    uia_candidate_id: str | None = None
    current_value: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


class GroundedButton(BaseModel):
    """One button found in the form."""

    model_config = ConfigDict(extra="ignore")

    button_id: str = Field(min_length=1)
    label: str = ""
    role: str = "other"
    normalized_bbox: NormalizedBox
    uia_candidate_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class ColumnMapping(BaseModel):
    """One proposed Excel column -> field mapping."""

    model_config = ConfigDict(extra="ignore")

    column: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = ""


class GroundingResponse(BaseModel):
    """The raw grounding response, validated against the schema."""

    model_config = ConfigDict(extra="ignore")

    screen_description: str = ""
    form_detected: bool
    fields: list[GroundedField] = Field(default_factory=list)
    buttons: list[GroundedButton] = Field(default_factory=list)
    column_mapping: list[ColumnMapping] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    layout_confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class GroundedElement:
    """A field or button resolved to physical screen coordinates."""

    element_id: str
    label: str
    kind: Literal["field", "button"]
    type: str
    rect: Rect
    uia_candidate_id: str | None
    confidence: float


@dataclass(frozen=True)
class GroundingResult:
    """A validated field map plus the prompt and response that produced it."""

    response: GroundingResponse
    fields: dict[str, GroundedElement]
    buttons: dict[str, GroundedElement]
    column_mapping: tuple[ColumnMapping, ...]
    unmapped_columns: tuple[str, ...]
    unmapped_fields: tuple[str, ...]
    warnings: tuple[str, ...]
    layout_confidence: float
    prompt: str = ""
    ai: AIResponse | None = None
    image_size: tuple[int, int] = (0, 0)

    def field_for_column(self, column: str) -> GroundedElement | None:
        """Return the field mapped from *column*, if any."""
        for item in self.column_mapping:
            if item.column == column:
                return self.fields.get(item.field_id)
        return None


def _resolve_element(
    element_id: str,
    label: str,
    kind: Literal["field", "button"],
    type_name: str,
    box: NormalizedBox,
    candidate_id: str | None,
    confidence: float,
    candidates_by_id: dict[str, UIACandidate],
    capture: Capture,
) -> tuple[GroundedElement | None, list[str], list[str]]:
    """Resolve one element, returning ``(element, warnings, problems)``."""
    warnings: list[str] = []
    problems: list[str] = []

    try:
        visual = normalized_to_screen(box.x1, box.y1, box.x2, box.y2, capture)
    except CoordinateError as exc:
        problems.append(f"{element_id}: {exc}")
        return None, warnings, problems

    rect = visual
    if candidate_id is not None:
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            problems.append(f"{element_id}: unknown UIA candidate {candidate_id!r}")
            return None, warnings, problems
        rect = candidate.rect
        if not visual.intersects(rect):
            warnings.append(
                f"{element_id}: visual box {visual.bbox} does not overlap UIA "
                f"candidate {candidate_id} {rect.bbox}; using the UIA rectangle"
            )

    if not rect_within(rect, capture.window.rect):
        problems.append(
            f"{element_id}: rectangle {rect.bbox} is outside the captured "
            f"window {capture.window.rect.bbox}"
        )

    return (
        GroundedElement(
            element_id=element_id,
            label=label,
            kind=kind,
            type=type_name,
            rect=rect,
            uia_candidate_id=candidate_id,
            confidence=confidence,
        ),
        warnings,
        problems,
    )


def parse_grounding(
    data: dict[str, Any],
    *,
    candidates: Sequence[UIACandidate],
    headers: Sequence[str],
    capture: Capture,
    config: GeminiConfig,
    prompt: str = "",
    ai: AIResponse | None = None,
    image_size: tuple[int, int] = (0, 0),
) -> GroundingResult:
    """Validate a raw grounding response and resolve it to screen rectangles."""
    try:
        response = GroundingResponse.model_validate(data)
    except ValidationError as exc:
        raise GroundingError(
            f"the model response does not match the schema: {exc}"
        ) from exc

    if not response.form_detected:
        raise GroundingError("no data-entry form was detected in the target window")

    problems: list[str] = []
    warnings: list[str] = list(response.warnings)
    candidates_by_id = {item.candidate_id: item for item in candidates}

    if not response.fields:
        problems.append("the model returned no text/number fields")

    fields: dict[str, GroundedElement] = {}
    seen_field_ids: set[str] = set()
    for field in response.fields:
        if field.field_id in seen_field_ids:
            problems.append(f"duplicate field_id {field.field_id!r}")
            continue
        seen_field_ids.add(field.field_id)
        element, element_warnings, element_problems = _resolve_element(
            field.field_id,
            field.label,
            "field",
            field.type,
            field.normalized_bbox,
            field.uia_candidate_id,
            field.confidence,
            candidates_by_id,
            capture,
        )
        warnings.extend(element_warnings)
        problems.extend(element_problems)
        if element is not None:
            fields[field.field_id] = element

    buttons: dict[str, GroundedElement] = {}
    seen_button_ids: set[str] = set()
    for button in response.buttons:
        if button.button_id in seen_button_ids:
            problems.append(f"duplicate button_id {button.button_id!r}")
            continue
        seen_button_ids.add(button.button_id)
        element, element_warnings, element_problems = _resolve_element(
            button.button_id,
            button.label,
            "button",
            button.role,
            button.normalized_bbox,
            button.uia_candidate_id,
            button.confidence,
            candidates_by_id,
            capture,
        )
        warnings.extend(element_warnings)
        problems.extend(element_problems)
        if element is not None:
            buttons[button.button_id] = element

    header_list = list(headers)
    header_set = set(header_list)
    seen_columns: dict[str, str] = {}
    seen_fields: dict[str, str] = {}
    for item in response.column_mapping:
        if item.column not in header_set:
            problems.append(
                f"mapped column {item.column!r} is not an Excel header"
            )
        if item.field_id not in fields:
            problems.append(
                f"mapped field_id {item.field_id!r} is not a detected field"
            )
        if item.column in seen_columns:
            problems.append(f"column {item.column!r} is mapped more than once")
        if item.field_id in seen_fields:
            problems.append(
                f"field {item.field_id!r} is mapped from more than one column"
            )
        seen_columns[item.column] = item.field_id
        seen_fields[item.field_id] = item.column

    mapped_columns = {item.column for item in response.column_mapping}
    unmapped_columns = tuple(
        header for header in header_list if header not in mapped_columns
    )
    mapped_fields = {item.field_id for item in response.column_mapping}
    unmapped_fields = tuple(
        field.field_id
        for field in response.fields
        if field.field_id not in mapped_fields
    )

    if set(response.unmapped_columns) != set(unmapped_columns):
        warnings.append(
            "the model's unmapped_columns did not match the computed set; "
            f"using {list(unmapped_columns)!r}"
        )
    if set(response.unmapped_fields) != set(unmapped_fields):
        warnings.append(
            "the model's unmapped_fields did not match the computed set; "
            f"using {list(unmapped_fields)!r}"
        )

    if response.layout_confidence < config.min_layout_confidence:
        warnings.append(
            f"layout confidence {response.layout_confidence:.2f} is below the "
            f"threshold {config.min_layout_confidence:.2f}; re-capture the "
            "window or confirm the mapping manually"
        )

    if problems:
        raise GroundingError(
            "invalid grounding response:\n  - " + "\n  - ".join(problems)
        )

    return GroundingResult(
        response=response,
        fields=fields,
        buttons=buttons,
        column_mapping=tuple(response.column_mapping),
        unmapped_columns=unmapped_columns,
        unmapped_fields=unmapped_fields,
        warnings=tuple(warnings),
        layout_confidence=response.layout_confidence,
        prompt=prompt,
        ai=ai,
        image_size=image_size,
    )


def ground_window(
    client: GeminiClient,
    capture: Capture,
    candidates: Sequence[UIACandidate],
    headers: Sequence[str],
    config: GeminiConfig,
) -> GroundingResult:
    """Send one screenshot + UIA candidates + Excel headers to Gemini."""
    image = prepare_image(capture.image, config.max_image_dimension)
    prompt = build_grounding_prompt(
        headers, candidates, image.width, image.height
    )
    ai = client.generate_json(prompt, image)
    return parse_grounding(
        ai.data,
        candidates=candidates,
        headers=headers,
        capture=capture,
        config=config,
        prompt=prompt,
        ai=ai,
        image_size=(image.width, image.height),
    )
