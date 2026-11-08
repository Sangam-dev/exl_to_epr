"""Column -> field mapping validation and persistence (Segment 5).

The grounded proposal is validated here before it can be approved. The
approved mapping is saved to ``data/state/mapping.json`` and reused by later
segments, so a run never depends on a fresh AI decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import project_root
from ..excel.reader import ExcelRow, format_value
from ..models.screen import Rect
from .grounding import GroundedElement, GroundingResult

MAPPING_VERSION = 1
DEFAULT_MAPPING_PATH = project_root() / "data" / "state" / "mapping.json"


class MappingError(RuntimeError):
    """A mapping cannot be built, validated, saved, or loaded."""


class MappingEntry(BaseModel):
    """One approved Excel column -> form field mapping."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    label: str = ""
    type: Literal["text", "number"]
    uia_candidate_id: str | None = None
    rect: tuple[int, int, int, int]
    confidence: float = Field(default=0.0, ge=0, le=1)

    @property
    def box(self) -> Rect:
        """The screen rectangle of the target field."""
        return Rect(*self.rect)


class ButtonEntry(BaseModel):
    """One approved button, normally the Save button."""

    model_config = ConfigDict(extra="forbid")

    button_id: str = Field(min_length=1)
    label: str = ""
    role: str = "other"
    uia_candidate_id: str | None = None
    rect: tuple[int, int, int, int]

    @property
    def box(self) -> Rect:
        """The screen rectangle of the button."""
        return Rect(*self.rect)


class ApprovedMapping(BaseModel):
    """The approved mapping file contents."""

    model_config = ConfigDict(extra="forbid")

    version: int = MAPPING_VERSION
    approved_at: str = ""
    model: str = ""
    window_title: str = ""
    process_name: str = ""
    image_size: tuple[int, int] = (0, 0)
    layout_confidence: float = Field(default=0.0, ge=0, le=1)
    entries: list[MappingEntry] = Field(default_factory=list)
    buttons: list[ButtonEntry] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)

    def field_for_column(self, column: str) -> MappingEntry | None:
        for entry in self.entries:
            if entry.column == column:
                return entry
        return None

    def button_for_role(self, role: str) -> ButtonEntry | None:
        for button in self.buttons:
            if button.role == role:
                return button
        return None


@dataclass(frozen=True)
class MappingValidation:
    """Errors block approval; warnings require the user's confirmation."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    unmapped_columns: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _first_number_problem(
    column: str, rows: Sequence[ExcelRow]
) -> str | None:
    for row in rows:
        try:
            format_value(row.values.get(column), "number")
        except ValueError as exc:
            return (
                f"column {column!r} is mapped as a number but row "
                f"{row.row_number} has {exc}"
            )
    return None


def validate_mapping(
    mapping: ApprovedMapping,
    *,
    headers: Sequence[str],
    fields: dict[str, GroundedElement],
    rows: Sequence[ExcelRow] = (),
    required_columns: Sequence[str] = (),
    min_confidence: float = 0.6,
) -> MappingValidation:
    """Check a proposed mapping against the workbook and the grounded form."""
    errors: list[str] = []
    warnings: list[str] = []

    header_list = list(headers)
    header_set = set(header_list)

    for column in required_columns:
        if column not in header_set:
            errors.append(
                f"required column {column!r} is not an Excel column available "
                "for mapping"
            )

    if not mapping.entries:
        errors.append("no Excel columns are mapped to form fields")

    seen_columns: dict[str, str] = {}
    seen_fields: dict[str, str] = {}
    for entry in mapping.entries:
        if entry.column not in header_set:
            errors.append(
                f"mapped column {entry.column!r} is not an Excel column "
                "available for mapping"
            )
        if entry.column in seen_columns:
            errors.append(f"column {entry.column!r} is mapped more than once")
        seen_columns[entry.column] = entry.field_id

        if entry.field_id in seen_fields:
            errors.append(
                f"field {entry.field_id!r} is assigned to more than one column"
            )
        seen_fields[entry.field_id] = entry.column

        element = fields.get(entry.field_id)
        if element is None:
            errors.append(
                f"field {entry.field_id!r} is not in the grounded form"
            )
        elif element.type != entry.type:
            errors.append(
                f"field {entry.field_id!r} is {element.type!r} in the form "
                f"but {entry.type!r} in the mapping"
            )

        if entry.confidence < min_confidence:
            warnings.append(
                f"column {entry.column!r} has confidence "
                f"{entry.confidence:.2f}, below the threshold "
                f"{min_confidence:.2f}"
            )

        if entry.type == "number":
            problem = _first_number_problem(entry.column, rows)
            if problem is not None:
                errors.append(problem)

    for column in required_columns:
        if column in header_set and column not in seen_columns:
            errors.append(f"required column {column!r} is not mapped to a field")

    if mapping.layout_confidence < min_confidence:
        warnings.append(
            f"layout confidence {mapping.layout_confidence:.2f} is below the "
            f"threshold {min_confidence:.2f}"
        )

    unmapped = tuple(
        header for header in header_list if header not in seen_columns
    )
    return MappingValidation(
        errors=tuple(errors),
        warnings=tuple(warnings),
        unmapped_columns=unmapped,
    )


def build_mapping(
    result: GroundingResult,
    *,
    window_title: str,
    process_name: str,
    model: str,
) -> ApprovedMapping:
    """Turn a validated grounding result into an unapproved mapping."""
    entries: list[MappingEntry] = []
    for item in result.column_mapping:
        element = result.fields.get(item.field_id)
        if element is None:
            continue
        entries.append(
            MappingEntry(
                column=item.column,
                field_id=item.field_id,
                label=element.label,
                type=element.type,
                uia_candidate_id=element.uia_candidate_id,
                rect=element.rect.bbox,
                confidence=item.confidence,
            )
        )

    buttons = [
        ButtonEntry(
            button_id=element.element_id,
            label=element.label,
            role=element.type,
            uia_candidate_id=element.uia_candidate_id,
            rect=element.rect.bbox,
        )
        for element in result.buttons.values()
    ]

    return ApprovedMapping(
        model=model,
        window_title=window_title,
        process_name=process_name,
        image_size=result.image_size,
        layout_confidence=result.layout_confidence,
        entries=entries,
        buttons=buttons,
        unmapped_columns=list(result.unmapped_columns),
    )


def save_mapping(
    mapping: ApprovedMapping, path: str | Path | None = None
) -> Path:
    """Write an approved mapping; refuse to persist an unapproved one."""
    if not mapping.approved_at:
        raise MappingError("the mapping has not been approved")

    target = Path(path) if path is not None else DEFAULT_MAPPING_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(mapping.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise MappingError(f"cannot write mapping {target}: {exc}") from exc
    return target


def load_mapping(path: str | Path | None = None) -> ApprovedMapping:
    """Read and validate a previously approved mapping."""
    target = Path(path) if path is not None else DEFAULT_MAPPING_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingError(f"cannot read mapping {target}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MappingError(f"invalid JSON in mapping {target}: {exc}") from exc

    try:
        mapping = ApprovedMapping.model_validate(data)
    except ValidationError as exc:
        raise MappingError(f"invalid mapping {target}: {exc}") from exc

    if mapping.version != MAPPING_VERSION:
        raise MappingError(
            f"unsupported mapping version {mapping.version} in {target}; "
            f"expected {MAPPING_VERSION}"
        )
    return mapping
