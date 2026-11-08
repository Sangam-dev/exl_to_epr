"""Unit tests for atomic run-state persistence (Segment 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excel_agent.agent.state_store import (
    RUN_STATE_VERSION,
    RunRow,
    RunState,
    StateError,
    load_run_state,
    save_run_state,
)


def _state(**overrides) -> RunState:
    values = {
        "run_id": "20261108_130000",
        "config_path": "C:/project/config.json",
        "mapping_path": "C:/project/mapping.json",
        "mapping_approved_at": "2026-11-08T12:00:00+00:00",
        "input_path": "C:/project/in.xlsx",
        "output_path": "C:/project/out.xlsx",
        "window_title": "Form",
        "window_process": "test.exe",
        "window_handle": 123,
        "layout_fingerprint": "[[400,300],[]]",
        "pending": (4, 5, 6),
        "completed": (RunRow(4, "DONE", 1),),
    }
    values.update(overrides)
    return RunState(**values)


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    save_run_state(_state(), path)

    loaded = load_run_state(path)

    assert loaded is not None
    assert loaded.version == RUN_STATE_VERSION
    assert loaded.run_id == "20261108_130000"
    assert loaded.config_path == "C:/project/config.json"
    assert loaded.mapping_path == "C:/project/mapping.json"
    assert loaded.mapping_approved_at == "2026-11-08T12:00:00+00:00"
    assert loaded.input_path == "C:/project/in.xlsx"
    assert loaded.output_path == "C:/project/out.xlsx"
    assert loaded.window_title == "Form"
    assert loaded.window_process == "test.exe"
    assert loaded.window_handle == 123
    assert loaded.layout_fingerprint == "[[400,300],[]]"
    assert loaded.pending == (4, 5, 6)
    assert loaded.completed[0] == RunRow(4, "DONE", 1)
    assert loaded.stopped is False
    assert loaded.updated_at


def test_remaining_and_unfinished_follow_completed_rows() -> None:
    state = _state()
    assert state.remaining == (5, 6)
    assert state.unfinished

    finished = _state(completed=(RunRow(4, "DONE"), RunRow(5, "DONE"), RunRow(6, "DONE")))
    assert finished.remaining == ()
    assert not finished.unfinished


def test_a_stopped_run_is_unfinished_even_with_no_remaining_rows() -> None:
    state = _state(
        pending=(4,),
        completed=(RunRow(4, "DONE"),),
        stopped=True,
        stopped_row=None,
    )
    assert state.remaining == ()
    assert state.unfinished


def test_load_returns_none_when_the_file_is_missing(tmp_path: Path) -> None:
    assert load_run_state(tmp_path / "missing.json") is None


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(StateError, match="cannot parse"):
        load_run_state(path)


def test_load_rejects_a_non_object_payload(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(StateError, match="JSON object"):
        load_run_state(path)


def test_load_rejects_an_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    payload = _state().to_dict()
    payload["version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateError, match="version"):
        load_run_state(path)


def test_load_rejects_missing_required_paths(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    payload = _state().to_dict()
    del payload["input_path"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateError, match="input_path"):
        load_run_state(path)


def test_load_rejects_non_integer_pending_rows(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    payload = _state().to_dict()
    payload["pending"] = [4, "5"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateError, match="pending"):
        load_run_state(path)


def test_load_rejects_a_bad_completed_entry(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    payload = _state().to_dict()
    payload["completed"] = [{"status": "DONE"}]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateError, match="row_number"):
        load_run_state(path)


def test_load_rejects_a_bad_stopped_row(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    payload = _state().to_dict()
    payload["stopped_row"] = "4"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StateError, match="stopped_row"):
        load_run_state(path)


def test_save_sets_updated_at_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state" / "run_state.json"
    save_run_state(_state(), path)

    assert path.is_file()
    leftovers = list((tmp_path / "state").glob(".run_state_*"))
    assert leftovers == []


def test_save_keeps_an_explicit_updated_at(tmp_path: Path) -> None:
    path = tmp_path / "run_state.json"
    save_run_state(_state(updated_at="2026-01-01T00:00:00+00:00"), path)

    loaded = load_run_state(path)
    assert loaded is not None
    assert loaded.updated_at == "2026-01-01T00:00:00+00:00"
