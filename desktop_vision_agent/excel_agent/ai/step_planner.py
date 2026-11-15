"""One user-written instruction + screenshot -> concrete click/fill actions.

Multi-step mode asks the user, in their own words, what to do next ("click Add
New", "fill up all the form"). This module turns that one sentence into the two
actions the deterministic runner already knows: click a named button, or type
one Excel column into one named text field.

The AI runs once per step, while the user is watching and about to approve the
result. Nothing here is used when rows are replayed.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..config import GeminiConfig
from ..desktop.capture import Capture
from ..models.screen import UIACandidate
from ..utils.images import prepare_image
from .client import GeminiClient
from .prompts import format_candidates, load_prompt, render_prompt

BUTTON_TYPES = frozenset({"Button"})
EDITABLE_TYPES = frozenset({"Edit", "Document", "Spinner"})


class StepPlanError(RuntimeError):
    """The instruction could not be turned into actions on this screen."""


class StepUnsupported(StepPlanError):
    """The instruction needs an interaction multi-step mode cannot drive yet."""


@dataclass(frozen=True)
class PlannedFill:
    """One Excel column typed into one live text field."""

    column: str
    label: str
    confidence: float = 0.0


@dataclass(frozen=True)
class StepPlan:
    """The concrete actions one instruction resolves to."""

    kind: Literal["click", "fill"]
    label: str = ""
    fills: tuple[PlannedFill, ...] = ()
    confidence: float = 0.0

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column this step types into."""
        return tuple(fill.column for fill in self.fills)


class _Fill(BaseModel):
    model_config = ConfigDict(extra="ignore")

    column: str = ""
    field: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("column", "field", mode="before")
    @classmethod
    def _no_null_text(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("confidence", mode="before")
    @classmethod
    def _no_null_confidence(cls, value: Any) -> Any:
        return 0.0 if value is None else value


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["click", "fill", "unsupported", "unclear"] = "unclear"
    button: str = ""
    fills: list[_Fill] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("button", "reason", mode="before")
    @classmethod
    def _no_null_text(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("kind", mode="before")
    @classmethod
    def _no_null_kind(cls, value: Any) -> Any:
        return "unclear" if value is None else value

    @field_validator("fills", mode="before")
    @classmethod
    def _no_null_fills(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("confidence", mode="before")
    @classmethod
    def _no_null_confidence(cls, value: Any) -> Any:
        return 0.0 if value is None else value


def _normalise(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _named(
    candidates: Sequence[UIACandidate],
    control_types: frozenset[str],
) -> list[UIACandidate]:
    return [
        item
        for item in candidates
        if item.control_type in control_types and item.name.strip()
    ]


def match_label(
    candidates: Sequence[UIACandidate],
    label: str,
    control_types: frozenset[str],
) -> UIACandidate | None:
    """Return the live control whose name matches *label*, ignoring case."""
    wanted = _normalise(label)
    if not wanted:
        return None
    for item in _named(candidates, control_types):
        if _normalise(item.name) == wanted:
            return item
    return None


def suggestions(
    candidates: Sequence[UIACandidate],
    label: str,
    control_types: frozenset[str],
    limit: int = 4,
) -> list[str]:
    """Return the closest control names, for an error message."""
    items = _named(candidates, control_types)
    names = [item.name for item in items]
    normalised = [_normalise(name) for name in names]
    wanted = _normalise(label)

    picked: list[str] = []
    for close in difflib.get_close_matches(wanted, normalised, n=limit, cutoff=0.5):
        name = names[normalised.index(close)]
        if name not in picked:
            picked.append(name)
    if not picked:
        for name, key in zip(names, normalised):
            if name in picked:
                continue
            if wanted and (wanted in key or key in wanted):
                picked.append(name)
            if len(picked) >= limit:
                break
    return picked[:limit]


def parse_step_plan(
    data: dict[str, Any],
    *,
    candidates: Sequence[UIACandidate],
    headers: Sequence[str],
    editable_types: frozenset[str] = EDITABLE_TYPES,
    button_types: frozenset[str] = BUTTON_TYPES,
) -> StepPlan:
    """Validate one model response against the live controls and the headers."""
    try:
        response = _Response.model_validate(data)
    except ValidationError as exc:
        raise StepPlanError(
            f"the model response does not match the schema: {exc}"
        ) from exc

    if response.kind in ("unsupported", "unclear"):
        raise StepUnsupported(
            response.reason.strip() or "the instruction is not something this mode can do yet"
        )

    if response.kind == "click":
        label = response.button.strip()
        buttons = _named(candidates, button_types)
        if not label:
            message = "the instruction did not name a button on this screen"
            if buttons:
                message += "; this window has " + ", ".join(
                    repr(item.name) for item in buttons[:4]
                )
            raise StepPlanError(message)
        button = match_label(candidates, label, button_types)
        if button is None:
            if not buttons:
                raise StepPlanError(
                    f"no button named {label!r}, and this window exposes no "
                    "buttons to Windows UI Automation; keep the window "
                    "restored and in front, then add the step again"
                )
            message = f"no button on this screen is named {label!r}"
            near = suggestions(candidates, label, button_types)
            if near:
                message += "; it does have " + ", ".join(repr(name) for name in near)
            raise StepPlanError(message)
        return StepPlan(kind="click", label=button.name, confidence=response.confidence)

    if not response.fills:
        raise StepPlanError("the model returned no field to fill")
    if not _named(candidates, editable_types):
        raise StepPlanError(
            "this window exposes no text fields to Windows UI Automation; keep "
            "the window restored and in front, then add the step again"
        )

    header_set = set(headers)
    problems: list[str] = []
    by_column: dict[str, PlannedFill] = {}
    for item in response.fills:
        column = item.column.strip()
        label = item.field.strip() or column
        if not column:
            problems.append("one fill entry has no Excel column")
            continue
        if column not in header_set:
            near = difflib.get_close_matches(column, list(headers), n=3, cutoff=0.6)
            message = f"{column!r} is not an Excel column"
            if near:
                message += "; did you mean " + ", ".join(repr(name) for name in near) + "?"
            problems.append(message)
            continue
        field = match_label(candidates, label, editable_types)
        if field is None:
            message = f"no text field on this screen is named {label!r}"
            near = suggestions(candidates, label, editable_types)
            if near:
                message += "; it does have " + ", ".join(repr(name) for name in near)
            problems.append(message)
            continue
        by_column[column] = PlannedFill(
            column=column,
            label=field.name,
            confidence=item.confidence or response.confidence,
        )

    if problems:
        raise StepPlanError("; ".join(problems))
    if not by_column:
        raise StepPlanError("the model filled no column")

    used_labels: dict[str, None] = {}
    fills: list[PlannedFill] = []
    for fill in by_column.values():
        key = _normalise(fill.label)
        if key in used_labels:
            continue
        used_labels[key] = None
        fills.append(fill)
    return StepPlan(kind="fill", fills=tuple(fills), confidence=response.confidence)


def build_step_prompt(
    instruction: str,
    headers: Sequence[str],
    candidates: Sequence[UIACandidate],
    image_width: int,
    image_height: int,
    correction: str = "",
) -> str:
    """Render the step prompt for one instruction."""
    candidate_list = list(candidates)
    header_list = list(headers)
    template = load_prompt("steps")
    return render_prompt(
        template,
        {
            "INSTRUCTION": instruction.strip() or "(none)",
            "CORRECTION": correction.strip() or "(none)",
            "CANDIDATES": format_candidates(candidate_list),
            "CANDIDATE_COUNT": str(len(candidate_list)),
            "HEADERS": "\n".join(f"- {header}" for header in header_list)
            or "(no columns)",
            "IMAGE_WIDTH": str(image_width),
            "IMAGE_HEIGHT": str(image_height),
        },
    )


def plan_step(
    client: GeminiClient,
    capture: Capture,
    candidates: Sequence[UIACandidate],
    headers: Sequence[str],
    instruction: str,
    config: GeminiConfig,
    *,
    correction: str = "",
) -> StepPlan:
    """Ask Gemini once what the instruction means on this screen."""
    image = prepare_image(capture.image, config.max_image_dimension)
    prompt = build_step_prompt(
        instruction,
        headers,
        candidates,
        image.width,
        image.height,
        correction,
    )
    response = client.generate_json(prompt, image)
    return parse_step_plan(response.data, candidates=candidates, headers=headers)


__all__ = [
    "BUTTON_TYPES",
    "EDITABLE_TYPES",
    "PlannedFill",
    "StepPlan",
    "StepPlanError",
    "StepUnsupported",
    "build_step_prompt",
    "match_label",
    "parse_step_plan",
    "plan_step",
    "suggestions",
]
