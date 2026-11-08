"""Unit tests for working-copy selection (Segment 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from excel_agent.agent.workspace import (
    WorkspaceError,
    load_workspace_state,
    open_workspace,
    save_workspace_state,
)
from excel_agent.config import AgentConfig


def _make_source(path: Path, rows: list[list] | None = None) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rows"
    sheet.append(["ID", "Name", "Amount"])
    for row in rows or [["A1", "Acme", 10], ["A2", "Beta", 20]]:
        sheet.append(row)
    workbook.save(path)
    return path


def _config(source: Path, output: Path) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Test"},
            "excel": {
                "input_path": str(source),
                "output_path": str(output),
                "sheet": "Rows",
                "header_row": 1,
                "id_column": "ID",
            },
            "fields": [
                {"column": "Name", "field": "Name", "type": "text"},
                {"column": "Amount", "field": "Amount", "type": "number"},
            ],
            "actions": {"save_button": "Save"},
        }
    )


def test_open_workspace_creates_and_records_a_copy(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path / "output.xlsx")
    state = tmp_path / "state" / "output.json"

    workspace = open_workspace(config, state)

    assert workspace.created
    assert workspace.path == tmp_path / "output.xlsx"
    assert workspace.path.is_file()

    recorded = load_workspace_state(state)
    assert recorded is not None
    assert recorded.input_path == str(source.resolve())
    assert recorded.output_path == str(workspace.path)


def test_open_workspace_reuses_the_recorded_copy(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path / "output.xlsx")
    state = tmp_path / "state" / "output.json"

    first = open_workspace(config, state)
    workbook = load_workbook(first.path)
    workbook["Rows"]["B2"] = "Edited"
    workbook.save(first.path)

    second = open_workspace(config, state)

    assert not second.created
    assert second.path == first.path
    assert load_workbook(second.path)["Rows"]["B2"].value == "Edited"


def test_open_workspace_recreates_when_the_copy_is_missing(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "input.xlsx")
    config = _config(source, tmp_path / "output.xlsx")
    state = tmp_path / "state" / "output.json"

    first = open_workspace(config, state)
    first.path.unlink()

    second = open_workspace(config, state)

    assert second.created
    assert second.path.is_file()
    assert second.path == first.path


def test_open_workspace_starts_over_for_a_different_input(tmp_path: Path) -> None:
    source_a = _make_source(tmp_path / "a.xlsx")
    source_b = _make_source(tmp_path / "b.xlsx")
    output = tmp_path / "output.xlsx"
    state = tmp_path / "state" / "output.json"

    first = open_workspace(_config(source_a, output), state)
    second = open_workspace(_config(source_b, output), state)

    assert second.created
    assert second.path != first.path
    recorded = load_workspace_state(state)
    assert recorded is not None
    assert recorded.input_path == str(source_b.resolve())


def test_load_workspace_state_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_workspace_state(tmp_path / "missing.json") is None


def test_load_workspace_state_rejects_malformed_json(tmp_path: Path) -> None:
    state = tmp_path / "output.json"
    state.write_text("not json", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="cannot read"):
        load_workspace_state(state)


def test_load_workspace_state_rejects_missing_keys(tmp_path: Path) -> None:
    state = tmp_path / "output.json"
    state.write_text(json.dumps({"version": 1}), encoding="utf-8")

    with pytest.raises(WorkspaceError, match="missing input_path"):
        load_workspace_state(state)


def test_save_workspace_state_writes_version_and_paths(tmp_path: Path) -> None:
    from excel_agent.agent.workspace import WorkspaceState

    state = tmp_path / "state" / "output.json"
    save_workspace_state(
        WorkspaceState(input_path="in.xlsx", output_path="out.xlsx"), state
    )

    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["input_path"] == "in.xlsx"
    assert data["output_path"] == "out.xlsx"
    assert data["updated_at"]
