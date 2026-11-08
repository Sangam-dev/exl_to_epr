"""Unit tests for run lifecycle and resume preflight (Segment 8)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from excel_agent.agent import orchestrator
from excel_agent.agent.calibration import ResolvedLayout, ResolvedTarget
from excel_agent.agent.orchestrator import (
    OrchestratorError,
    RunSession,
    resume_preflight,
    resume_rows,
    resume_run,
    start_run,
    validate_resume,
)
from excel_agent.agent.row_runner import RowOutcome, RunResult
from excel_agent.agent.state_store import (
    RunRow,
    RunState,
    load_run_state,
    save_run_state,
)
from excel_agent.agent.workspace import Workspace
from excel_agent.ai.mapping import ApprovedMapping, ButtonEntry, MappingEntry
from excel_agent.config import AgentConfig, resolve_path
from excel_agent.desktop.capture import Capture
from excel_agent.desktop.uia import UIAInspection
from excel_agent.excel.reader import ExcelRow
from excel_agent.models.screen import Rect, WindowInfo


def _window(width: int = 400, height: int = 300) -> WindowInfo:
    return WindowInfo(
        handle=123,
        title="Form",
        process_name="test.exe",
        class_name="Form",
        rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        dpi=96,
    )


def _row(
    number: int, status: str = "", values: dict | None = None
) -> ExcelRow:
    return ExcelRow(
        row_number=number,
        values=values if values is not None else {"Invoice Number": f"INV-{number}"},
        status=status,
    )


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Form"},
            "excel": {
                "input_path": str(tmp_path / "in.xlsx"),
                "output_path": str(tmp_path / "out.xlsx"),
                "sheet": "Rows",
                "id_column": "Invoice Number",
            },
            "fields": [
                {
                    "column": "Invoice Number",
                    "field": "Invoice Number",
                    "type": "text",
                }
            ],
            "actions": {"save_button": "Save"},
        }
    )


def _mapping(
    *, trusted: bool = True, approved_at: str = "2026-11-08T12:00:00+00:00"
) -> ApprovedMapping:
    return ApprovedMapping(
        approved_at=approved_at,
        trusted=trusted,
        trusted_at=approved_at if trusted else "",
        entries=[
            MappingEntry(
                column="Invoice Number",
                field_id="f1",
                label="Invoice Number",
                type="text",
                uia_candidate_id="W1",
                rect=(10, 10, 110, 30),
                confidence=1.0,
            )
        ],
        buttons=[
            ButtonEntry(
                button_id="b1",
                label="Save",
                role="save",
                uia_candidate_id="W9",
                rect=(200, 200, 260, 240),
            )
        ],
    )


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path / "out.xlsx", created=False)


def _start(
    tmp_path: Path,
    *,
    rows: list[ExcelRow] | None = None,
    mapping: ApprovedMapping | None = None,
) -> RunSession:
    return start_run(
        config_path=tmp_path / "config.json",
        mapping_path=tmp_path / "mapping.json",
        config=_config(tmp_path),
        mapping=mapping or _mapping(),
        workspace=_workspace(tmp_path),
        window=_window(),
        rows=rows if rows is not None else [_row(4), _row(5)],
        path=tmp_path / "state" / "run_state.json",
    )


# ---------------------------------------------------------------------------
# Starting and recording
# ---------------------------------------------------------------------------


def test_start_run_records_paths_and_selected_rows(tmp_path: Path) -> None:
    session = _start(tmp_path)

    loaded = load_run_state(session.path)
    assert loaded is not None
    assert loaded.pending == (4, 5)
    assert loaded.config_path == str((tmp_path / "config.json").resolve())
    assert loaded.mapping_path == str((tmp_path / "mapping.json").resolve())
    assert loaded.input_path == str((tmp_path / "in.xlsx").resolve())
    assert loaded.output_path == str((tmp_path / "out.xlsx").resolve())
    assert loaded.window_title == "Form"
    assert loaded.window_process == "test.exe"
    assert loaded.window_handle == 123
    assert loaded.mapping_approved_at == "2026-11-08T12:00:00+00:00"
    assert loaded.unfinished


def test_record_persists_after_every_row(tmp_path: Path) -> None:
    session = _start(tmp_path)

    session.record(RowOutcome(4, "DONE", 1))
    after_first = load_run_state(session.path)
    assert after_first is not None
    assert after_first.completed_numbers == {4}
    assert after_first.remaining == (5,)

    session.record(RowOutcome(5, "RECHECK", 2, "could not verify"))
    after_second = load_run_state(session.path)
    assert after_second is not None
    assert after_second.completed_numbers == {4, 5}
    assert after_second.remaining == ()
    assert after_second.completed[1].error == "could not verify"


def test_recording_the_same_row_replaces_its_previous_outcome(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path)

    session.record(RowOutcome(4, "RECHECK", 1, "first"))
    session.record(RowOutcome(4, "DONE", 2))

    loaded = load_run_state(session.path)
    assert loaded is not None
    assert len(loaded.completed) == 1
    assert loaded.completed[0] == RunRow(4, "DONE", 2)


def test_finish_marks_the_run_stopped(tmp_path: Path) -> None:
    session = _start(tmp_path)
    session.record(RowOutcome(4, "DONE", 1))

    session.finish(
        RunResult(
            outcomes=(RowOutcome(4, "DONE", 1),),
            stopped=True,
            stopped_row=5,
            stop_reason="hotkey: stop",
        )
    )

    loaded = load_run_state(session.path)
    assert loaded is not None
    assert loaded.stopped
    assert loaded.stopped_row == 5
    assert loaded.stop_reason == "hotkey: stop"
    assert loaded.remaining == (5,)


def test_finish_clears_a_previous_stop(tmp_path: Path) -> None:
    session = _start(tmp_path)
    session.abort("interrupted")

    session.finish(RunResult(outcomes=(RowOutcome(4, "DONE", 1),)))

    loaded = load_run_state(session.path)
    assert loaded is not None
    assert not loaded.stopped
    assert loaded.stopped_row is None
    assert loaded.stop_reason == ""


def test_abort_points_at_the_first_remaining_row(tmp_path: Path) -> None:
    session = _start(tmp_path)
    session.record(RowOutcome(4, "DONE", 1))

    session.abort("interrupted")

    loaded = load_run_state(session.path)
    assert loaded is not None
    assert loaded.stopped
    assert loaded.stopped_row == 5
    assert loaded.stop_reason == "interrupted"


# ---------------------------------------------------------------------------
# Resume validation
# ---------------------------------------------------------------------------


def _validate(
    tmp_path: Path,
    state: RunState,
    *,
    config: AgentConfig | None = None,
    mapping: ApprovedMapping | None = None,
    workspace: Workspace | None = None,
    rows: list[ExcelRow] | None = None,
) -> None:
    validate_resume(
        state,
        config_path=tmp_path / "config.json",
        mapping_path=tmp_path / "mapping.json",
        config=config or _config(tmp_path),
        mapping=mapping or _mapping(),
        workspace=workspace or _workspace(tmp_path),
        rows=rows if rows is not None else [_row(4), _row(5)],
    )


def test_validate_resume_accepts_a_matching_run(tmp_path: Path) -> None:
    session = _start(tmp_path)
    _validate(tmp_path, session.state)


def test_validate_resume_rejects_a_different_config(tmp_path: Path) -> None:
    session = _start(tmp_path)
    state = replace(session.state, config_path=str(tmp_path / "other.json"))

    with pytest.raises(OrchestratorError, match="different config"):
        _validate(tmp_path, state)


def test_validate_resume_rejects_a_different_mapping(tmp_path: Path) -> None:
    session = _start(tmp_path)
    state = replace(session.state, mapping_path=str(tmp_path / "other.json"))

    with pytest.raises(OrchestratorError, match="different mapping"):
        _validate(tmp_path, state)


def test_validate_resume_rejects_a_different_input(tmp_path: Path) -> None:
    session = _start(tmp_path)
    state = replace(session.state, input_path=str(tmp_path / "other.xlsx"))

    with pytest.raises(OrchestratorError, match="different input workbook"):
        _validate(tmp_path, state)


def test_validate_resume_rejects_a_different_working_copy(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path)
    state = replace(session.state, output_path=str(tmp_path / "other.xlsx"))

    with pytest.raises(OrchestratorError, match="different working copy"):
        _validate(tmp_path, state)


def test_validate_resume_rejects_an_untrusted_mapping(tmp_path: Path) -> None:
    session = _start(tmp_path)

    with pytest.raises(OrchestratorError, match="not trusted"):
        _validate(tmp_path, session.state, mapping=_mapping(trusted=False))


def test_validate_resume_rejects_a_reapproved_mapping(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path)

    with pytest.raises(OrchestratorError, match="re-approved"):
        _validate(
            tmp_path,
            session.state,
            mapping=_mapping(approved_at="2026-11-08T13:00:00+00:00"),
        )


def test_validate_resume_rejects_a_missing_row(tmp_path: Path) -> None:
    session = _start(tmp_path, rows=[_row(4), _row(5)])
    state = replace(session.state, pending=(4, 9))

    with pytest.raises(OrchestratorError, match="no longer contains row"):
        _validate(tmp_path, state, rows=[_row(4)])


def test_validate_resume_rejects_a_recorded_row_without_a_status(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path)
    session.record(RowOutcome(4, "DONE", 1))

    with pytest.raises(OrchestratorError, match="no status"):
        _validate(
            tmp_path,
            session.state,
            rows=[_row(4, status=""), _row(5)],
        )


def test_validate_resume_rejects_a_completed_row_outside_the_run(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path)
    state = replace(
        session.state,
        completed=(RunRow(99, "DONE", 1),),
    )

    with pytest.raises(OrchestratorError, match="not part of this run"):
        _validate(tmp_path, state)


def test_resume_run_requires_a_state_file(tmp_path: Path) -> None:
    with pytest.raises(OrchestratorError, match="no run state"):
        resume_run(
            state_path=tmp_path / "missing.json",
            config_path=tmp_path / "config.json",
            mapping_path=tmp_path / "mapping.json",
            config=_config(tmp_path),
            mapping=_mapping(),
            workspace=_workspace(tmp_path),
            rows=[_row(4)],
        )


# ---------------------------------------------------------------------------
# Resume row selection
# ---------------------------------------------------------------------------


def test_resume_rows_skips_completed_and_status_bearing_rows(
    tmp_path: Path,
) -> None:
    session = _start(tmp_path, rows=[_row(4), _row(5), _row(6)])
    session.record(RowOutcome(4, "DONE", 1))

    selected, skipped = resume_rows(
        session.state,
        [
            _row(4, status="DONE"),
            _row(5),
            _row(6, status="RECHECK"),
        ],
    )

    assert [row.row_number for row in selected] == [5]
    assert [row.row_number for row in skipped] == [6]


def test_resume_rows_keeps_the_original_order(tmp_path: Path) -> None:
    session = _start(tmp_path, rows=[_row(6), _row(4), _row(5)])

    selected, skipped = resume_rows(
        session.state, [_row(4), _row(5), _row(6)]
    )

    assert [row.row_number for row in selected] == [6, 4, 5]
    assert skipped == []


def test_resume_rows_rejects_a_missing_row(tmp_path: Path) -> None:
    session = _start(tmp_path, rows=[_row(4), _row(5)])
    state = replace(session.state, pending=(4, 9))

    with pytest.raises(OrchestratorError, match="no longer contains row 9"):
        resume_rows(state, [_row(4)])


# ---------------------------------------------------------------------------
# Resume preflight
# ---------------------------------------------------------------------------


def _capture(window: WindowInfo) -> Capture:
    return Capture(
        image=Image.new("RGB", (400, 300), "white"),
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=window,
    )


def _layout() -> ResolvedLayout:
    return ResolvedLayout(
        fields={
            "Invoice Number": ResolvedTarget(
                key="Invoice Number",
                rect=Rect(10, 10, 110, 30),
                candidate_id="W1",
                source="uia-id",
            )
        },
        save=ResolvedTarget(
            key="save",
            rect=Rect(200, 200, 260, 240),
            candidate_id="W9",
            source="uia-id",
        ),
    )


def _preflight_setup(monkeypatch, tmp_path: Path):
    session = _start(tmp_path)
    window = _window()
    inspection = UIAInspection(window=window, candidates=(), _controls={})
    monkeypatch.setattr(orchestrator, "capture_window", lambda _w: _capture(window))
    monkeypatch.setattr(orchestrator, "inspect_window", lambda _w: inspection)
    monkeypatch.setattr(orchestrator, "resolve_layout", lambda _m, _i: _layout())
    return session, window


def test_resume_preflight_saves_a_screenshot_and_fingerprint(
    monkeypatch, tmp_path: Path
) -> None:
    session, window = _preflight_setup(monkeypatch, tmp_path)

    preflight = resume_preflight(
        session,
        config=_config(tmp_path),
        mapping=_mapping(),
        window=window,
        screenshot_dir=tmp_path / "shots",
    )

    assert preflight.screenshot.is_file()
    assert preflight.changed is False
    assert preflight.fingerprint
    assert session.state.layout_fingerprint == preflight.fingerprint
    loaded = load_run_state(session.path)
    assert loaded is not None
    assert loaded.layout_fingerprint == preflight.fingerprint


def test_resume_preflight_reports_a_changed_layout(
    monkeypatch, tmp_path: Path
) -> None:
    session, window = _preflight_setup(monkeypatch, tmp_path)
    session.record_layout("a-different-layout")

    preflight = resume_preflight(
        session,
        config=_config(tmp_path),
        mapping=_mapping(),
        window=window,
        screenshot_dir=tmp_path / "shots",
    )

    assert preflight.changed is True


def test_resume_preflight_stops_on_a_layout_problem(
    monkeypatch, tmp_path: Path
) -> None:
    session, window = _preflight_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orchestrator,
        "resolve_layout",
        lambda _m, _i: ResolvedLayout(
            fields={}, save=None, problems=("Invoice Number: missing",)
        ),
    )

    with pytest.raises(OrchestratorError, match="does not match"):
        resume_preflight(
            session,
            config=_config(tmp_path),
            mapping=_mapping(),
            window=window,
            screenshot_dir=tmp_path / "shots",
        )

    assert list((tmp_path / "shots").glob("*.png"))


def test_resume_preflight_saves_state_through_the_session(
    tmp_path: Path,
) -> None:
    session = RunSession(
        RunState(
            config_path=str((tmp_path / "config.json").resolve()),
            mapping_path=str((tmp_path / "mapping.json").resolve()),
            input_path=str((tmp_path / "in.xlsx").resolve()),
            output_path=str((tmp_path / "out.xlsx").resolve()),
            pending=(4,),
        ),
        tmp_path / "run_state.json",
    )
    session.record_layout("fingerprint")
    session.save()

    loaded = load_run_state(tmp_path / "run_state.json")
    assert loaded is not None
    assert loaded.layout_fingerprint == "fingerprint"
