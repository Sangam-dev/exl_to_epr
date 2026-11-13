"""Unit tests for recovering from a stale stopped run (Segment 8)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from excel_agent.agent.state_store import RunState, load_run_state, save_run_state
from excel_agent.agent.workspace import (
    WorkspaceState,
    load_workspace_state,
    save_workspace_state,
)
from excel_agent.ai.mapping import ApprovedMapping
from excel_agent.cli import (
    _archive_run_state,
    _prefer_recorded_copy,
    _stale_run_reason,
)
from excel_agent.config import AgentConfig


def _config(input_path: Path, output_path: Path) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Test"},
            "excel": {
                "input_path": str(input_path),
                "output_path": str(output_path),
                "sheet": "Rows",
                "id_column": "ID",
            },
            "fields": [{"column": "Name", "field": "Name", "type": "text"}],
            "actions": {"save_button": "Save"},
        }
    )


def _mapping(approved_at: str = "2026-11-10T10:00:00+05:45") -> ApprovedMapping:
    return ApprovedMapping(approved_at=approved_at, trusted=True)


def _state(
    *,
    input_path: Path,
    output_path: Path,
    config_path: Path,
    mapping_path: Path,
    mapping_approved_at: str = "2026-11-08T18:53:19+05:45",
) -> RunState:
    return RunState(
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        mapping_approved_at=mapping_approved_at,
        input_path=str(input_path),
        output_path=str(output_path),
        pending=(2, 3),
        stopped=True,
        stopped_row=2,
        stop_reason="layout changed",
    )


def test_stale_run_reason_is_empty_when_everything_matches(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    output = tmp_path / "output.xlsx"
    source.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    config_path = tmp_path / "config.json"

    reason = _stale_run_reason(
        _state(
            input_path=source,
            output_path=output,
            config_path=config_path,
            mapping_path=mapping_path,
            mapping_approved_at="2026-11-10T10:00:00+05:45",
        ),
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        config=_config(source, output),
        mapping=_mapping(),
    )

    assert reason == ""


def test_stale_run_reason_detects_a_reapproved_mapping(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    output = tmp_path / "output.xlsx"
    source.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    config_path = tmp_path / "config.json"

    reason = _stale_run_reason(
        _state(
            input_path=source,
            output_path=output,
            config_path=config_path,
            mapping_path=mapping_path,
        ),
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        config=_config(source, output),
        mapping=_mapping(),
    )

    assert "re-approved" in reason


def test_stale_run_reason_detects_a_changed_input(tmp_path: Path) -> None:
    old_input = tmp_path / "old.xlsx"
    new_input = tmp_path / "new.xlsx"
    output = tmp_path / "output.xlsx"
    for path in (old_input, new_input, output):
        path.write_text("", encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    config_path = tmp_path / "config.json"

    reason = _stale_run_reason(
        _state(
            input_path=old_input,
            output_path=output,
            config_path=config_path,
            mapping_path=mapping_path,
            mapping_approved_at="2026-11-10T10:00:00+05:45",
        ),
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        config=_config(new_input, output),
        mapping=_mapping(),
    )

    assert "input workbook changed" in reason


def test_stale_run_reason_keeps_a_run_whose_copy_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    output = tmp_path / "missing.xlsx"
    source.write_text("", encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    config_path = tmp_path / "config.json"

    reason = _stale_run_reason(
        _state(
            input_path=source,
            output_path=output,
            config_path=config_path,
            mapping_path=mapping_path,
            mapping_approved_at="2026-11-10T10:00:00+05:45",
        ),
        config_path=str(config_path),
        mapping_path=str(mapping_path),
        config=_config(source, tmp_path / "output.xlsx"),
        mapping=_mapping(),
    )

    # A missing working copy must block the run, not be auto-discarded.
    assert reason == ""


def test_archive_run_state_preserves_the_file(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    save_run_state(_state_with_output(tmp_path), path)

    archived = _archive_run_state(path)

    assert not path.exists()
    assert archived.is_file()
    assert load_run_state(archived) is not None


def test_prefer_recorded_copy_repairs_the_pointer(tmp_path: Path) -> None:
    source = tmp_path / "input.xlsx"
    output = tmp_path / "output.xlsx"
    source.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    config = _config(source, output)
    state = tmp_path / "output.json"

    save_workspace_state(
        WorkspaceState(
            input_path=str(source.resolve()),
            output_path=str(tmp_path / "elsewhere.xlsx"),
        ),
        state,
    )

    adopted = _prefer_recorded_copy(
        _state(
            input_path=source,
            output_path=output,
            config_path=tmp_path / "config.json",
            mapping_path=tmp_path / "mapping.json",
        ),
        config,
        state,
    )

    assert adopted == output.resolve()
    pointer = load_workspace_state(state)
    assert pointer is not None
    assert pointer.output_path == str(output.resolve())


def _state_with_output(tmp_path: Path) -> RunState:
    source = tmp_path / "input.xlsx"
    source.write_text("", encoding="utf-8")
    workbook = Workbook()
    workbook.save(tmp_path / "output.xlsx")
    return _state(
        input_path=source,
        output_path=tmp_path / "output.xlsx",
        config_path=tmp_path / "config.json",
        mapping_path=tmp_path / "mapping.json",
    )
