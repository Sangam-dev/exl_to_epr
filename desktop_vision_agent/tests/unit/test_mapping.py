"""Unit tests for mapping validation, approval, and persistence (Segment 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excel_agent.agent.setup_flow import (
    ApprovalOutcome,
    approve_mapping,
    edit_mapping,
    print_mapping,
)
from excel_agent.ai.grounding import (
    ColumnMapping,
    GroundedElement,
    GroundingResponse,
    GroundingResult,
)
from excel_agent.ai.mapping import (
    ApprovedMapping,
    ButtonEntry,
    MappingEntry,
    MappingError,
    MappingValidation,
    build_mapping,
    load_mapping,
    save_mapping,
    validate_mapping,
)
from excel_agent.excel.reader import ExcelRow
from excel_agent.models.screen import Rect


def _field(
    field_id: str = "f1",
    label: str = "Invoice Number",
    type_name: str = "text",
    rect: tuple[int, int, int, int] = (10, 20, 110, 40),
) -> GroundedElement:
    return GroundedElement(
        element_id=field_id,
        label=label,
        kind="field",
        type=type_name,
        rect=Rect(*rect),
        uia_candidate_id="W1",
        confidence=0.9,
    )


def _button() -> GroundedElement:
    return GroundedElement(
        element_id="b1",
        label="Save",
        kind="button",
        type="save",
        rect=Rect(200, 300, 260, 340),
        uia_candidate_id="W9",
        confidence=0.9,
    )


def _entry(
    column: str = "Invoice Number",
    field_id: str = "f1",
    type_name: str = "text",
    confidence: float = 0.95,
) -> MappingEntry:
    return MappingEntry(
        column=column,
        field_id=field_id,
        label=column,
        type=type_name,
        uia_candidate_id="W1",
        rect=(10, 20, 110, 40),
        confidence=confidence,
    )


def _mapping(
    entries: list[MappingEntry] | None = None,
    *,
    layout_confidence: float = 0.9,
) -> ApprovedMapping:
    return ApprovedMapping(
        layout_confidence=layout_confidence,
        entries=entries if entries is not None else [_entry()],
        buttons=[
            ButtonEntry(
                button_id="b1",
                label="Save",
                role="save",
                uia_candidate_id="W9",
                rect=(200, 300, 260, 340),
            )
        ],
        unmapped_columns=["Notes"],
    )


def _result(
    fields: dict[str, GroundedElement] | None = None,
    *,
    layout_confidence: float = 0.9,
) -> GroundingResult:
    fields = fields if fields is not None else {"f1": _field()}
    response = GroundingResponse(
        form_detected=True,
        layout_confidence=layout_confidence,
    )
    return GroundingResult(
        response=response,
        fields=fields,
        buttons={"b1": _button()},
        column_mapping=(
            ColumnMapping(column="Invoice Number", field_id="f1", confidence=0.95),
        ),
        unmapped_columns=("Notes",),
        unmapped_fields=(),
        warnings=(),
        layout_confidence=layout_confidence,
        image_size=(800, 600),
    )


def _validate(
    mapping: ApprovedMapping,
    *,
    fields: dict[str, GroundedElement] | None = None,
    headers: list[str] | None = None,
    rows: list[ExcelRow] | None = None,
    required: list[str] | None = None,
    min_confidence: float = 0.6,
) -> MappingValidation:
    return validate_mapping(
        mapping,
        headers=["Invoice Number", "Notes"] if headers is None else headers,
        fields={"f1": _field()} if fields is None else fields,
        rows=[] if rows is None else rows,
        required_columns=[] if required is None else required,
        min_confidence=min_confidence,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_mapping_accepts_a_clean_mapping() -> None:
    validation = _validate(_mapping())

    assert validation.ok
    assert validation.errors == ()
    assert validation.warnings == ()
    assert validation.unmapped_columns == ("Notes",)


def test_validate_mapping_rejects_a_missing_required_column() -> None:
    validation = _validate(_mapping(), required=["Missing Column"])

    assert not validation.ok
    assert any("is not an Excel column available" in e for e in validation.errors)


def test_validate_mapping_rejects_an_unmapped_required_column() -> None:
    validation = _validate(_mapping(), required=["Notes"])

    assert not validation.ok
    assert any("is not mapped to a field" in e for e in validation.errors)


def test_validate_mapping_rejects_a_duplicate_column() -> None:
    mapping = _mapping([_entry(), _entry(field_id="f2")])

    validation = _validate(
        mapping,
        fields={"f1": _field(), "f2": _field("f2", "Other")},
    )

    assert not validation.ok
    assert any("mapped more than once" in e for e in validation.errors)


def test_validate_mapping_rejects_a_duplicate_field() -> None:
    mapping = _mapping(
        [_entry(), _entry(column="Notes")]
    )

    validation = _validate(mapping)

    assert not validation.ok
    assert any("assigned to more than one column" in e for e in validation.errors)


def test_validate_mapping_rejects_a_type_mismatch() -> None:
    mapping = _mapping([_entry(type_name="number")])

    validation = _validate(mapping, fields={"f1": _field(type_name="text")})

    assert not validation.ok
    assert any("in the form but 'number' in the mapping" in e for e in validation.errors)


def test_validate_mapping_rejects_an_unknown_field() -> None:
    mapping = _mapping([_entry(field_id="f9")])

    validation = _validate(mapping)

    assert not validation.ok
    assert any("is not in the grounded form" in e for e in validation.errors)


def test_validate_mapping_rejects_a_non_numeric_value_in_a_number_column() -> None:
    mapping = _mapping([_entry(type_name="number")])
    rows = [ExcelRow(row_number=2, values={"Invoice Number": "abc"})]

    validation = _validate(
        mapping,
        fields={"f1": _field(type_name="number")},
        rows=rows,
    )

    assert not validation.ok
    assert any("row 2" in e and "not a number" in e for e in validation.errors)


def test_validate_mapping_warns_on_low_mapping_confidence() -> None:
    mapping = _mapping([_entry(confidence=0.2)])

    validation = _validate(mapping, min_confidence=0.6)

    assert validation.ok
    assert any("below the threshold" in w for w in validation.warnings)


def test_validate_mapping_warns_on_low_layout_confidence() -> None:
    mapping = _mapping(layout_confidence=0.2)

    validation = _validate(mapping, min_confidence=0.6)

    assert validation.ok
    assert any("layout confidence" in w for w in validation.warnings)


def test_validate_mapping_rejects_an_empty_mapping() -> None:
    validation = _validate(_mapping([]))

    assert not validation.ok
    assert any("no Excel columns" in e for e in validation.errors)


def test_mapping_validation_ok_only_cares_about_errors() -> None:
    assert MappingValidation(errors=("x",)).ok is False
    assert MappingValidation(warnings=("x",)).ok is True


# ---------------------------------------------------------------------------
# Build and persistence
# ---------------------------------------------------------------------------


def test_build_mapping_converts_the_grounding_result() -> None:
    mapping = build_mapping(
        _result(),
        window_title="Invoice Form",
        process_name="app.exe",
        model="gemini-3.6-flash",
    )

    assert mapping.window_title == "Invoice Form"
    assert mapping.process_name == "app.exe"
    assert mapping.model == "gemini-3.6-flash"
    assert mapping.image_size == (800, 600)
    assert mapping.approved_at == ""
    assert mapping.entries[0].column == "Invoice Number"
    assert mapping.entries[0].field_id == "f1"
    assert mapping.entries[0].rect == (10, 20, 110, 40)
    assert mapping.buttons[0].role == "save"
    assert mapping.unmapped_columns == ["Notes"]


def test_build_mapping_lookup_helpers() -> None:
    mapping = build_mapping(
        _result(),
        window_title="Invoice Form",
        process_name="app.exe",
        model="gemini-3.6-flash",
    )

    assert mapping.field_for_column("Invoice Number") is mapping.entries[0]
    assert mapping.field_for_column("Notes") is None
    assert mapping.button_for_role("save") is mapping.buttons[0]
    assert mapping.button_for_role("cancel") is None


def test_save_mapping_refuses_an_unapproved_mapping(tmp_path: Path) -> None:
    with pytest.raises(MappingError, match="not been approved"):
        save_mapping(_mapping(), tmp_path / "mapping.json")


def test_save_and_load_mapping_roundtrip(tmp_path: Path) -> None:
    approved = _mapping().model_copy(
        update={"approved_at": "2026-11-07T12:00:00+00:00"}
    )
    path = tmp_path / "mapping.json"

    saved = save_mapping(approved, path)
    loaded = load_mapping(path)

    assert saved == path
    assert loaded.model_dump() == approved.model_dump()


def test_load_mapping_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(MappingError, match="cannot read"):
        load_mapping(tmp_path / "missing.json")


def test_load_mapping_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(MappingError, match="invalid JSON"):
        load_mapping(path)


def test_load_mapping_rejects_an_invalid_schema(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(
        json.dumps({"version": 1, "entries": "not a list"}),
        encoding="utf-8",
    )

    with pytest.raises(MappingError, match="invalid mapping"):
        load_mapping(path)


def test_load_mapping_rejects_an_unsupported_version(tmp_path: Path) -> None:
    data = _mapping().model_copy(update={"approved_at": "now"}).model_dump()
    data["version"] = 99
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MappingError, match="unsupported mapping version"):
        load_mapping(path)


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def test_approve_mapping_accepts_yes() -> None:
    outcome = approve_mapping(
        _mapping(),
        MappingValidation(),
        input_fn=lambda _: "y",
        output=lambda _: None,
    )

    assert isinstance(outcome, ApprovalOutcome)
    assert outcome.approved
    assert outcome.mapping is not None


def test_approve_mapping_rejects_no() -> None:
    outcome = approve_mapping(
        _mapping(),
        MappingValidation(),
        input_fn=lambda _: "n",
        output=lambda _: None,
    )

    assert not outcome.approved
    assert "not approved" in outcome.reason


def test_approve_mapping_refuses_errors_without_prompting() -> None:
    def _explode(_: str) -> str:
        raise AssertionError("should not prompt when there are errors")

    outcome = approve_mapping(
        _mapping(),
        MappingValidation(errors=("bad",)),
        input_fn=_explode,
        output=lambda _: None,
    )

    assert not outcome.approved
    assert "errors" in outcome.reason


def test_approve_mapping_requires_explicit_approval_for_warnings() -> None:
    validation = MappingValidation(warnings=("low confidence",))

    rejected = approve_mapping(
        _mapping(),
        validation,
        input_fn=lambda _: "y",
        output=lambda _: None,
    )
    accepted = approve_mapping(
        _mapping(),
        validation,
        input_fn=lambda _: "approve",
        output=lambda _: None,
    )

    assert not rejected.approved
    assert accepted.approved


def test_approve_mapping_assume_yes_accepts_warnings() -> None:
    outcome = approve_mapping(
        _mapping(),
        MappingValidation(warnings=("low confidence",)),
        assume_yes=True,
        output=lambda _: None,
    )

    assert outcome.approved


def test_approve_mapping_treats_eof_as_cancelled() -> None:
    def _eof(_: str) -> str:
        raise EOFError

    outcome = approve_mapping(
        _mapping(),
        MappingValidation(),
        input_fn=_eof,
        output=lambda _: None,
    )

    assert not outcome.approved
    assert outcome.reason == "no input"


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def _inputs(values: list[str]):
    iterator = iter(values)
    return lambda _: next(iterator)


def test_edit_mapping_assigns_a_column_to_a_field() -> None:
    mapping = _mapping([])
    fields = {
        "f1": _field(),
        "f2": _field("f2", "Customer Name"),
    }

    edited = edit_mapping(
        mapping,
        headers=["Invoice Number", "Customer Name"],
        fields=fields,
        input_fn=_inputs(["set Invoice Number f2", "done"]),
        output=lambda _: None,
    )

    assert edited is not None
    entry = edited.field_for_column("Invoice Number")
    assert entry is not None
    assert entry.field_id == "f2"
    assert entry.type == "text"
    assert entry.confidence == 1.0


def test_edit_mapping_removes_a_column() -> None:
    edited = edit_mapping(
        _mapping(),
        headers=["Invoice Number"],
        fields={"f1": _field()},
        input_fn=_inputs(["remove Invoice Number", "done"]),
        output=lambda _: None,
    )

    assert edited is not None
    assert edited.entries == []


def test_edit_mapping_keeps_the_mapping_one_to_one() -> None:
    mapping = _mapping(
        [
            _entry("Invoice Number", "f1"),
            _entry("Customer Name", "f2"),
        ]
    )
    fields = {
        "f1": _field(),
        "f2": _field("f2", "Customer Name"),
    }

    edited = edit_mapping(
        mapping,
        headers=["Invoice Number", "Customer Name"],
        fields=fields,
        input_fn=_inputs(["set Customer Name f1", "done"]),
        output=lambda _: None,
    )

    assert edited is not None
    assert len(edited.entries) == 1
    assert edited.entries[0].column == "Customer Name"
    assert edited.entries[0].field_id == "f1"


def test_edit_mapping_rejects_an_unknown_column() -> None:
    output: list[str] = []

    edited = edit_mapping(
        _mapping(),
        headers=["Invoice Number"],
        fields={"f1": _field()},
        input_fn=_inputs(["set Missing f1", "done"]),
        output=output.append,
    )

    assert edited is not None
    assert len(edited.entries) == 1
    assert any("does not start with a known column" in line for line in output)


def test_edit_mapping_rejects_an_unknown_field() -> None:
    output: list[str] = []

    edited = edit_mapping(
        _mapping(),
        headers=["Invoice Number"],
        fields={"f1": _field()},
        input_fn=_inputs(["set Invoice Number f9", "done"]),
        output=output.append,
    )

    assert edited is not None
    assert any("is not a grounded field" in line for line in output)


def test_edit_mapping_remove_reports_an_unmapped_column() -> None:
    output: list[str] = []

    edited = edit_mapping(
        _mapping([]),
        headers=["Invoice Number"],
        fields={"f1": _field()},
        input_fn=_inputs(["remove Invoice Number", "done"]),
        output=output.append,
    )

    assert edited is not None
    assert any("was not mapped" in line for line in output)


def test_edit_mapping_cancel_returns_none() -> None:
    assert (
        edit_mapping(
            _mapping(),
            headers=["Invoice Number"],
            fields={"f1": _field()},
            input_fn=_inputs(["cancel"]),
            output=lambda _: None,
        )
        is None
    )


def test_edit_mapping_eof_returns_none() -> None:
    def _eof(_: str) -> str:
        raise EOFError

    assert (
        edit_mapping(
            _mapping(),
            headers=["Invoice Number"],
            fields={"f1": _field()},
            input_fn=_eof,
            output=lambda _: None,
        )
        is None
    )


def test_edit_mapping_show_prints_the_current_mapping() -> None:
    output: list[str] = []

    edit_mapping(
        _mapping(),
        headers=["Invoice Number"],
        fields={"f1": _field()},
        input_fn=_inputs(["show", "done"]),
        output=output.append,
    )

    assert any("Proposed mapping" in line for line in output)


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def test_print_mapping_includes_entries_buttons_and_unmapped_columns() -> None:
    output: list[str] = []

    print_mapping(
        _mapping(),
        fields={"f1": _field()},
        validation=MappingValidation(
            errors=("an error",),
            warnings=("a warning",),
        ),
        output=output.append,
    )

    text = "\n".join(output)
    assert "Proposed mapping (1 column(s))" in text
    assert "Invoice Number" in text
    assert "Buttons (1)" in text
    assert "Unmapped columns: Notes" in text
    assert "Errors (1)" in text
    assert "Warnings (1)" in text
