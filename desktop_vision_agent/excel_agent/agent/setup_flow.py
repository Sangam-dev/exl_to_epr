"""Grounding -> mapping proposal -> user approval (Segment 5).

The flow is deliberately small:

1. show the proposed mapping, the grounded fields, and the unmapped columns;
2. let the user reassign or remove mappings with a few text commands;
3. validate again, then require an explicit approval;
4. save the approved mapping so later segments never depend on the AI again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ..ai.grounding import GroundedElement
from ..ai.mapping import (
    ApprovedMapping,
    MappingEntry,
    MappingValidation,
)

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


@dataclass(frozen=True)
class ApprovalOutcome:
    """The result of asking the user to approve a mapping."""

    approved: bool
    mapping: ApprovedMapping | None = None
    reason: str = ""


def print_mapping(
    mapping: ApprovedMapping,
    *,
    fields: dict[str, GroundedElement] | None = None,
    validation: MappingValidation | None = None,
    output: OutputFn = print,
) -> None:
    """Print the proposed mapping in a compact, readable table."""
    output("")
    output(f"Proposed mapping ({len(mapping.entries)} column(s)):")
    if mapping.entries:
        for entry in mapping.entries:
            output(
                f"  {entry.column:<28} -> {entry.field_id:<4} "
                f"{entry.label or '-':<28} {entry.type:<7} {entry.confidence:.2f}"
            )
    else:
        output("  (none)")

    if fields:
        output("")
        output(f"Grounded fields ({len(fields)}):")
        for element in fields.values():
            output(
                f"  {element.element_id:<4} {element.label or '-':<28} "
                f"{element.type:<7} {element.rect.bbox}"
            )

    if mapping.buttons:
        output("")
        output(f"Buttons ({len(mapping.buttons)}):")
        for button in mapping.buttons:
            output(
                f"  {button.button_id:<4} {button.label or '-':<28} "
                f"{button.role:<7} {button.rect}"
            )

    output("")
    output(
        "Unmapped columns: "
        + (", ".join(mapping.unmapped_columns) or "(none)")
    )

    if validation is not None:
        if validation.errors:
            output("")
            output(f"Errors ({len(validation.errors)}):")
            for error in validation.errors:
                output(f"  - {error}")
        if validation.warnings:
            output("")
            output(f"Warnings ({len(validation.warnings)}):")
            for warning in validation.warnings:
                output(f"  - {warning}")


def _match_header(text: str, headers: Sequence[str]) -> tuple[str | None, str]:
    """Split *text* into a known header and the remaining argument.

    The longest matching header wins, so a header containing spaces works
    without quoting: ``set Invoice Number f1``.
    """
    candidates = [
        header
        for header in headers
        if text == header or text.startswith(header + " ")
    ]
    if not candidates:
        return None, text
    header = max(candidates, key=len)
    return header, text[len(header) :].strip()


def _replace_entry(
    mapping: ApprovedMapping,
    entry: MappingEntry,
) -> ApprovedMapping:
    """Return a copy with *entry* assigned, keeping the mapping one-to-one."""
    entries = [
        existing
        for existing in mapping.entries
        if existing.column != entry.column and existing.field_id != entry.field_id
    ]
    entries.append(entry)
    return mapping.model_copy(update={"entries": entries})


def edit_mapping(
    mapping: ApprovedMapping,
    *,
    headers: Sequence[str],
    fields: dict[str, GroundedElement],
    input_fn: InputFn = input,
    output: OutputFn = print,
) -> ApprovedMapping | None:
    """Interactively edit a mapping. Returns ``None`` when the user cancels."""
    current = mapping

    output("")
    output("Edit commands:")
    output("  set <column> <field_id>   assign a column to a grounded field")
    output("  remove <column>           remove a mapping")
    output("  show                      print the current mapping")
    output("  done                      finish editing")
    output("  cancel                    abort setup")

    while True:
        try:
            raw = input_fn("edit> ").strip()
        except EOFError:
            return None

        if not raw:
            continue

        command, _, rest = raw.partition(" ")
        command = command.lower()
        rest = rest.strip()

        if command == "cancel":
            return None
        if command == "done":
            return current
        if command == "show":
            print_mapping(current, fields=fields, output=output)
            continue

        if command == "set":
            column, field_id = _match_header(rest, headers)
            if column is None:
                output(f"ERROR: {rest!r} does not start with a known column")
                continue
            if not field_id:
                output("usage: set <column> <field_id>")
                continue
            element = fields.get(field_id)
            if element is None:
                output(f"ERROR: {field_id!r} is not a grounded field")
                continue
            current = _replace_entry(
                current,
                MappingEntry(
                    column=column,
                    field_id=field_id,
                    label=element.label,
                    type=element.type,
                    uia_candidate_id=element.uia_candidate_id,
                    rect=element.rect.bbox,
                    confidence=1.0,
                ),
            )
            output(f"Set {column!r} -> {field_id} ({element.type})")
            continue

        if command == "remove":
            column, extra = _match_header(rest, headers)
            if column is None:
                output(f"ERROR: {rest!r} does not start with a known column")
                continue
            if extra:
                output("usage: remove <column>")
                continue
            kept = [entry for entry in current.entries if entry.column != column]
            if len(kept) == len(current.entries):
                output(f"{column!r} was not mapped")
            else:
                current = current.model_copy(update={"entries": kept})
                output(f"Removed {column!r}")
            continue

        output(f"ERROR: unknown command {command!r}; use set, remove, show, done, or cancel")


def approve_mapping(
    mapping: ApprovedMapping,
    validation: MappingValidation,
    *,
    assume_yes: bool = False,
    input_fn: InputFn = input,
    output: OutputFn = print,
) -> ApprovalOutcome:
    """Ask for approval; warnings need an explicit acknowledgement."""
    if not validation.ok:
        return ApprovalOutcome(
            approved=False,
            reason="the mapping has errors and cannot be approved",
        )

    if validation.warnings:
        output("")
        output("This mapping has warnings:")
        for warning in validation.warnings:
            output(f"  - {warning}")
        if assume_yes:
            output("--yes: accepting the warnings.")
        else:
            try:
                answer = input_fn(
                    "Type 'approve' to accept the warnings, anything else to cancel: "
                ).strip().lower()
            except EOFError:
                return ApprovalOutcome(approved=False, reason="no input")
            if answer != "approve":
                return ApprovalOutcome(
                    approved=False, reason="the warnings were not accepted"
                )
    elif not assume_yes:
        try:
            answer = input_fn("Approve this mapping? [y/N]: ").strip().lower()
        except EOFError:
            return ApprovalOutcome(approved=False, reason="no input")
        if answer not in {"y", "yes"}:
            return ApprovalOutcome(approved=False, reason="not approved")

    return ApprovalOutcome(approved=True, mapping=mapping)


__all__ = [
    "ApprovalOutcome",
    "approve_mapping",
    "edit_mapping",
    "print_mapping",
]
