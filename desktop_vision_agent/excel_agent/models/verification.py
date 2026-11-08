"""Pre-save and post-save verification models (Segment 6).

The model responses are strict Pydantic schemas. The verdicts add the
deterministic cross-checks performed by :mod:`excel_agent.ai.verification`
and are what the calibration loop consumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class VerificationError(RuntimeError):
    """A verification response is unusable or contradicts itself."""


class FieldCheck(BaseModel):
    """One mapped field as seen in the pre-save screenshot."""

    model_config = ConfigDict(extra="ignore")

    field_id: str = ""
    column: str = ""
    expected: str = ""
    observed: str = ""
    ok: bool = False
    note: str = ""


class PreSaveResponse(BaseModel):
    """The raw pre-save response, validated against the schema."""

    model_config = ConfigDict(extra="ignore")

    approved: bool
    confidence: float = Field(default=0.0, ge=0, le=1)
    field_checks: list[FieldCheck] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class PostSaveResponse(BaseModel):
    """The raw post-save response, validated against the schema."""

    model_config = ConfigDict(extra="ignore")

    approved: bool
    saved: bool
    confidence: float = Field(default=0.0, ge=0, le=1)
    form_cleared: bool | None = None
    evidence: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PreSaveVerdict:
    """A pre-save response plus the issues computed by the parser."""

    approved: bool
    response: PreSaveResponse
    issues: tuple[str, ...] = ()
    prompt: str = ""
    ai_log: str | None = None


@dataclass(frozen=True)
class PostSaveVerdict:
    """A post-save response plus the issues computed by the parser."""

    approved: bool
    saved: bool
    response: PostSaveResponse
    issues: tuple[str, ...] = ()
    prompt: str = ""
    ai_log: str | None = None


__all__ = [
    "FieldCheck",
    "PostSaveResponse",
    "PostSaveVerdict",
    "PreSaveResponse",
    "PreSaveVerdict",
    "VerificationError",
]
