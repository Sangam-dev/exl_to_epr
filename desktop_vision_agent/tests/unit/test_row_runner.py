"""Unit tests for the batch row runner (Segment 7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from excel_agent.agent import row_runner
from excel_agent.agent.calibration import ResolvedLayout, ResolvedTarget, SaveEvidence
from excel_agent.agent.row_runner import (
    AMBIGUOUS,
    NOT_SAVED,
    SAVED,
    RowOutcome,
    RunnerAborted,
    RunnerError,
    classify_save,
    layout_fingerprint,
    run_row,
    run_rows,
)
from excel_agent.ai.mapping import ApprovedMapping, ButtonEntry, MappingEntry
from excel_agent.config import AgentConfig
from excel_agent.desktop.capture import Capture
from excel_agent.desktop.uia import UIAInspection
from excel_agent.excel.reader import ExcelRow
from excel_agent.models.screen import Rect, WindowInfo
from excel_agent.models.verification import PostSaveResponse, PostSaveVerdict


def _window(width: int = 400, height: int = 300) -> WindowInfo:
    return WindowInfo(
        handle=1,
        title="Form",
        process_name="test.exe",
        class_name="Form",
        rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        dpi=96,
    )


def _inspection() -> UIAInspection:
    return UIAInspection(window=_window(), candidates=(), _controls={})


def _entry(
    column: str = "Invoice Number",
    field_id: str = "f1",
    *,
    label: str = "Invoice Number",
    type_name: str = "text",
    rect: tuple[int, int, int, int] = (10, 20, 110, 40),
) -> MappingEntry:
    return MappingEntry(
        column=column,
        field_id=field_id,
        label=label,
        type=type_name,
        uia_candidate_id="W1",
        rect=rect,
        confidence=1.0,
    )


def _save_button() -> ButtonEntry:
    return ButtonEntry(
        button_id="b1",
        label="Save",
        role="save",
        uia_candidate_id="W9",
        rect=(200, 200, 260, 240),
    )


def _mapping(
    *,
    trusted: bool = True,
    entries: list[MappingEntry] | None = None,
    buttons: list[ButtonEntry] | None = None,
) -> ApprovedMapping:
    return ApprovedMapping(
        approved_at="2026-11-08T12:00:00+00:00",
        trusted=trusted,
        trusted_at="2026-11-08T12:00:00+00:00" if trusted else "",
        entries=entries if entries is not None else [_entry()],
        buttons=buttons if buttons is not None else [_save_button()],
    )


def _config(
    *, max_attempts: int = 3, stop_on_failure: bool = False
) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Form"},
            "excel": {
                "input_path": "data/input/in.xlsx",
                "output_path": "data/output/out.xlsx",
                "sheet": "Rows",
                "id_column": "Invoice Number",
            },
            "fields": [],
            "actions": {"save_button": "Save", "wait_after_save_seconds": 0},
            "safety": {
                "max_attempts": max_attempts,
                "action_delay_seconds": 0,
                "stop_on_failure": stop_on_failure,
            },
        }
    )


def _row(
    row_number: int = 2, values: dict | None = None
) -> ExcelRow:
    return ExcelRow(
        row_number=row_number,
        values=values if values is not None else {"Invoice Number": "INV-0001"},
    )


def _capture(width: int = 400, height: int = 300) -> Capture:
    return Capture(
        image=Image.new("RGB", (width, height), "white"),
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=_window(width, height),
    )


def _post_verdict(
    approved: bool, saved: bool, issues: tuple[str, ...] = ()
) -> PostSaveVerdict:
    return PostSaveVerdict(
        approved=approved,
        saved=saved,
        response=PostSaveResponse(approved=approved, saved=saved),
        issues=issues,
    )


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    def write_status(
        self, row_number: int, status: str, error: str = "", timestamp=None
    ) -> None:
        self.calls.append((row_number, status, error))


def _stub_desktop(
    monkeypatch,
    *,
    evidence: SaveEvidence,
    post_verdict: PostSaveVerdict | None = None,
    stub_wait: bool = True,
    on_save=None,
) -> dict[str, int]:
    window = _window()
    inspection = _inspection()
    captures = [_capture(), _capture()]
    calls = {"save": 0, "post": 0, "capture": 0}

    def fake_capture(_window):
        index = min(calls["capture"], len(captures) - 1)
        calls["capture"] += 1
        return captures[index]

    def fake_save(_layout):
        calls["save"] += 1
        if on_save is not None:
            on_save()

    def fake_post(*_args, **_kwargs):
        calls["post"] += 1
        return post_verdict or _post_verdict(True, True)

    monkeypatch.setattr(row_runner, "attach_window", lambda _app: window)
    monkeypatch.setattr(row_runner, "inspect_window", lambda _window: inspection)
    monkeypatch.setattr(row_runner, "fill_row", lambda *a, **k: None)
    monkeypatch.setattr(row_runner, "read_back", lambda *a, **k: ([], []))
    monkeypatch.setattr(row_runner, "capture_window", fake_capture)
    monkeypatch.setattr(row_runner, "_click_save", fake_save)
    if stub_wait:
        monkeypatch.setattr(row_runner, "_wait", lambda *a, **k: True)
    monkeypatch.setattr(
        row_runner, "compare_saved_fields", lambda *a, **k: evidence
    )
    monkeypatch.setattr(row_runner, "check_post_save", fake_post)
    return calls


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_classify_save() -> None:
    assert (
        classify_save(
            SaveEvidence(unchanged=False, compared=("Invoice Number",))
        )
        == SAVED
    )
    assert (
        classify_save(
            SaveEvidence(unchanged=True, compared=("Invoice Number",))
        )
        == NOT_SAVED
    )
    assert classify_save(SaveEvidence(unchanged=False, compared=())) == AMBIGUOUS


def test_layout_fingerprint_tracks_geometry_not_candidate_ids() -> None:
    fields = {
        "Invoice Number": ResolvedTarget(
            key="Invoice Number",
            rect=Rect(10, 20, 110, 40),
            candidate_id="W1",
            source="uia-id",
        )
    }
    save = ResolvedTarget(
        key="save",
        rect=Rect(200, 200, 260, 240),
        candidate_id="W9",
        source="uia-id",
    )
    first = ResolvedLayout(fields=fields, save=save)

    renamed = ResolvedLayout(
        fields={
            "Invoice Number": ResolvedTarget(
                key="Invoice Number",
                rect=Rect(10, 20, 110, 40),
                candidate_id="W2",
                source="uia-name",
            )
        },
        save=ResolvedTarget(
            key="save",
            rect=Rect(200, 200, 260, 240),
            candidate_id="W8",
            source="uia-name",
        ),
    )
    moved = ResolvedLayout(
        fields={
            "Invoice Number": ResolvedTarget(
                key="Invoice Number",
                rect=Rect(30, 20, 130, 40),
                candidate_id="W1",
                source="uia-id",
            )
        },
        save=save,
    )

    window = Rect(0, 0, 400, 300)
    assert layout_fingerprint(first, window) == layout_fingerprint(renamed, window)
    assert layout_fingerprint(first, window) != layout_fingerprint(moved, window)


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def test_run_row_marks_done_without_asking_gemini(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=("Invoice Number",),
            detail="the filled fields changed",
        ),
    )
    writer = FakeWriter()

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert result.attempts == 1
    assert result.screenshots == ()
    assert writer.calls == [(2, "DONE", "")]
    assert calls == {"save": 1, "post": 0, "capture": 2}


def test_run_row_retries_when_the_fields_are_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=True,
            compared=("Invoice Number",),
            identical=("Invoice Number",),
            detail="the filled fields are unchanged",
        ),
    )
    writer = FakeWriter()

    result = run_row(
        _row(),
        config=_config(max_attempts=2),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 2
    assert calls["save"] == 2
    assert calls["post"] == 0
    assert "unchanged" in writer.calls[0][2]
    assert result.screenshots


def test_run_row_asks_gemini_when_the_diff_is_ambiguous(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=(),
            detail="no non-empty field could be compared",
        ),
        post_verdict=_post_verdict(True, True),
    )
    writer = FakeWriter()

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert result.attempts == 1
    assert calls["post"] == 1
    assert calls["save"] == 1


def test_run_row_does_not_retry_an_ambiguous_save_gemini_rejects(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=(),
            detail="the window size changed between captures",
        ),
        post_verdict=_post_verdict(
            False, False, ("the record is not visible",)
        ),
    )
    writer = FakeWriter()

    result = run_row(
        _row(),
        config=_config(max_attempts=3),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert calls["post"] == 1
    assert "may already exist" in writer.calls[0][2]


def test_run_row_retries_a_read_back_mismatch(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )
    monkeypatch.setattr(
        row_runner,
        "read_back",
        lambda *a, **k: (["Invoice Number: read back 'WRONG'"], []),
    )
    writer = FakeWriter()

    result = run_row(
        _row(),
        config=_config(max_attempts=2),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 2
    assert calls["save"] == 0


def test_run_row_rechecks_an_invalid_number(tmp_path: Path) -> None:
    mapping = _mapping(
        entries=[
            _entry(
                column="Amount",
                field_id="f3",
                label="Amount",
                type_name="number",
            )
        ]
    )
    writer = FakeWriter()

    result = run_row(
        _row(values={"Amount": "abc"}),
        config=_config(),
        mapping=mapping,
        writer=writer,
        client=object(),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 0
    assert "not a number" in result.error
    assert writer.calls[0][1] == "RECHECK"


def test_run_row_reports_a_layout_problem_as_fatal(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )
    monkeypatch.setattr(
        row_runner,
        "resolve_layout",
        lambda *a, **k: ResolvedLayout(
            fields={}, save=None, problems=("Invoice Number: missing",)
        ),
    )

    with pytest.raises(RunnerError, match="does not match"):
        run_row(
            _row(),
            config=_config(),
            mapping=_mapping(),
            writer=FakeWriter(),
            client=object(),
            screenshot_dir=tmp_path / "shots",
        )


def test_run_row_marks_recheck_when_stopped_after_save(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeController:
        def __init__(self) -> None:
            self.running = True

        def check(self) -> bool:
            return self.running

        def wait(self, seconds: float) -> bool:
            return self.running

    controller = FakeController()
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False, compared=("Invoice Number",)
        ),
        stub_wait=False,
        on_save=lambda: setattr(controller, "running", False),
    )

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        controller=controller,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert "stopped after Save" in result.error
    assert calls["post"] == 0


# ---------------------------------------------------------------------------
# Batch iteration
# ---------------------------------------------------------------------------


def test_run_rows_requires_a_trusted_mapping() -> None:
    with pytest.raises(RunnerError, match="not trusted"):
        run_rows(
            [_row()],
            config=_config(),
            mapping=_mapping(trusted=False),
            writer=FakeWriter(),
            client=object(),
        )


def test_run_rows_rejects_an_invalid_limit() -> None:
    with pytest.raises(RunnerError, match="limit"):
        run_rows(
            [_row()],
            config=_config(),
            mapping=_mapping(),
            writer=FakeWriter(),
            client=object(),
            limit=0,
        )


def test_run_rows_rejects_a_mapping_with_a_missing_column() -> None:
    with pytest.raises(RunnerError, match="missing from the workbook"):
        run_rows(
            [_row(values={"Amount": "10"})],
            config=_config(),
            mapping=_mapping(),
            writer=FakeWriter(),
            client=object(),
        )


def test_run_rows_processes_each_row_and_counts(
    monkeypatch,
) -> None:
    outcomes = {
        2: RowOutcome(2, "DONE", 1),
        4: RowOutcome(4, "RECHECK", 2, "failed"),
    }

    def fake_run_row(row, **_kwargs):
        return outcomes[row.row_number]

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
    )

    assert result.processed == 2
    assert result.count("DONE") == 1
    assert result.count("RECHECK") == 1
    assert not result.stopped


def test_run_rows_honours_the_limit(monkeypatch) -> None:
    seen: list[int] = []

    def fake_run_row(row, **_kwargs):
        seen.append(row.row_number)
        return RowOutcome(row.row_number, "DONE", 1)

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4), _row(5)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        limit=2,
    )

    assert seen == [2, 4]
    assert result.processed == 2


def test_run_rows_stops_after_a_recheck_when_configured(monkeypatch) -> None:
    seen: list[int] = []

    def fake_run_row(row, **_kwargs):
        seen.append(row.row_number)
        return RowOutcome(row.row_number, "RECHECK", 1)

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4)],
        config=_config(stop_on_failure=True),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
    )

    assert seen == [2]
    assert result.processed == 1


def test_run_rows_reports_an_aborted_row(monkeypatch) -> None:
    def fake_run_row(row, **_kwargs):
        raise RunnerAborted("stopped by the user")

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
    )

    assert result.stopped
    assert result.stopped_row == 2
    assert result.stop_reason == "stopped by the user"
    assert result.processed == 0
