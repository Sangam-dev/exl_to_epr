"""Unit tests for the batch row runner (Segment 7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from excel_agent.agent import pause_guard, row_runner
from excel_agent.agent.calibration import ResolvedLayout, ResolvedTarget, SaveEvidence
from excel_agent.agent.confirmation import (
    CONFIRMED,
    REJECTED,
    UNKNOWN,
    ConfirmationResult,
)
from excel_agent.agent.pending_status import PendingStatusFile, load_pending_status
from excel_agent.agent.row_runner import (
    RowOutcome,
    RunnerAborted,
    RunnerError,
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


class FakeWriter:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[int, str, str]] = []

    def write_status(
        self, row_number: int, status: str, error: str = "", timestamp=None
    ) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise OSError("the workbook is open in Excel")
        self.calls.append((row_number, status, error))


class FakeConfirmer:
    def __init__(
        self, result: ConfirmationResult, on_confirm=None
    ) -> None:
        self.result = result
        self.on_confirm = on_confirm
        self.images: list[object] = []
        self.weak_hints: list[str] = []

    def confirm(self, image, *, weak_hint: str = "") -> ConfirmationResult:
        self.images.append(image)
        self.weak_hints.append(weak_hint)
        if self.on_confirm is not None:
            self.on_confirm()
        return self.result


def _confirmation(
    outcome: str = CONFIRMED, reason: str = "", evidence: tuple[str, ...] = ()
) -> ConfirmationResult:
    return ConfirmationResult(
        outcome=outcome,
        reason=reason,
        evidence=evidence,
        source="fake",
    )


def _stub_desktop(
    monkeypatch,
    *,
    evidence: SaveEvidence,
    stub_wait: bool = True,
    on_save=None,
) -> dict[str, int]:
    window = _window()
    inspection = _inspection()
    captures = [_capture(), _capture()]
    calls = {"save": 0, "capture": 0}

    def fake_capture(_window):
        index = min(calls["capture"], len(captures) - 1)
        calls["capture"] += 1
        return captures[index]

    def fake_save(_layout, _window=None, **_kwargs):
        calls["save"] += 1
        if on_save is not None:
            on_save()

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
    return calls


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class _ResumingController:
    """Reports a pause-and-resume on a chosen check."""

    def __init__(self, resume_on: int) -> None:
        self._resume_on = resume_on
        self.pause_count = 0
        self.checks = 0

    def check(self) -> bool:
        self.checks += 1
        if self.checks >= self._resume_on:
            self.pause_count = 1
        return True


def test_a_resume_before_save_reads_the_screen_again(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=("Invoice Number",),
            detail="the filled fields changed",
        ),
    )
    stub_inspect = row_runner.inspect_window
    seen: list[int] = []
    monkeypatch.setattr(
        row_runner,
        "inspect_window",
        lambda window, **kwargs: seen.append(1) or stub_inspect(window, **kwargs),
    )
    focused: list[object] = []
    monkeypatch.setattr(
        pause_guard, "focus_window", lambda window: focused.append(window) or True
    )
    notes: list[str] = []

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        confirmation=FakeConfirmer(_confirmation(CONFIRMED, "success text matched")),
        controller=_ResumingController(resume_on=2),
        screenshot_dir=tmp_path / "shots",
        output=notes.append,
    )

    assert result.status == "DONE"
    assert calls["save"] == 1
    # Once at the start of the attempt, once more after the resume.
    assert len(seen) == 2
    assert len(focused) == 1
    assert "  resumed: the window was brought back to the front" in notes


def test_a_resume_that_cannot_raise_the_window_saves_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, detail="the filled fields changed"),
    )
    monkeypatch.setattr(pause_guard, "focus_window", lambda _window: False)

    with pytest.raises(RunnerAborted) as excinfo:
        run_row(
            _row(),
            config=_config(),
            mapping=_mapping(),
            writer=FakeWriter(),
            client=object(),
            confirmation=FakeConfirmer(_confirmation(CONFIRMED)),
            controller=_ResumingController(resume_on=2),
            screenshot_dir=tmp_path / "shots",
            output=lambda _m: None,
        )

    assert "could not be brought back to the front" in str(excinfo.value)
    assert calls["save"] == 0


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


def test_run_row_confirmed_marks_done(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=("Invoice Number",),
            detail="the filled fields changed",
        ),
    )
    writer = FakeWriter()
    confirmer = FakeConfirmer(_confirmation(CONFIRMED, "success text matched"))

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert result.attempts == 1
    assert result.screenshots == ()
    assert writer.calls == [(2, "DONE", "")]
    assert calls == {"save": 1, "capture": 2}
    assert len(confirmer.images) == 1
    assert confirmer.weak_hints == ["the filled fields changed"]


def test_run_row_confirmed_wins_even_when_the_fields_are_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=True,
            compared=("Invoice Number",),
            identical=("Invoice Number",),
            detail="the filled fields are unchanged",
        ),
    )
    writer = FakeWriter()
    confirmer = FakeConfirmer(_confirmation(CONFIRMED, "saved banner"))

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert writer.calls == [(2, "DONE", "")]


def test_pixel_change_alone_is_never_done(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=("Invoice Number",),
            detail="the filled fields changed",
        ),
    )
    writer = FakeWriter()
    confirmer = FakeConfirmer(_confirmation(UNKNOWN, "no success text"))

    result = run_row(
        _row(),
        config=_config(max_attempts=3),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert "record may already exist" in result.error
    assert writer.calls[0][1] == "RECHECK"
    assert confirmer.weak_hints == ["the filled fields changed"]


def test_run_row_does_not_save_again_when_the_fields_are_unchanged(
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
    confirmer = FakeConfirmer(_confirmation(UNKNOWN, "no success text"))

    result = run_row(
        _row(),
        config=_config(max_attempts=2),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert "may already exist" in writer.calls[0][2]
    assert result.screenshots


def test_run_row_rejects_a_rejected_save_and_never_retries(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(
            unchanged=False,
            compared=(),
            detail="the window size changed between captures",
        ),
    )
    writer = FakeWriter()
    confirmer = FakeConfirmer(
        _confirmation(REJECTED, "error text matched 'duplicate'")
    )

    result = run_row(
        _row(),
        config=_config(max_attempts=3),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert "duplicate" in writer.calls[0][2]
    assert "may already exist" in writer.calls[0][2]


def test_save_is_clicked_at_most_once_per_row(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )
    confirmer = FakeConfirmer(_confirmation(UNKNOWN, "no success text"))

    result = run_row(
        _row(),
        config=_config(max_attempts=5),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        confirmation=confirmer,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.attempts == 1
    assert calls["save"] == 1


def test_before_save_callback_runs_once_and_before_the_click(
    monkeypatch, tmp_path: Path
) -> None:
    order: list[str] = []
    _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
        on_save=lambda: order.append("save"),
    )

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        confirmation=FakeConfirmer(_confirmation(CONFIRMED)),
        on_before_save=lambda _row: order.append("intent"),
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert order == ["intent", "save"]


def test_confirm_each_declines_before_the_intent_and_save(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )
    seen: list[str] = []

    with pytest.raises(RunnerAborted, match="declined"):
        run_row(
            _row(),
            config=_config(),
            mapping=_mapping(),
            writer=FakeWriter(),
            client=object(),
            confirmation=FakeConfirmer(_confirmation(CONFIRMED)),
            confirm_each=True,
            input_func=lambda _prompt: "n",
            on_before_save=lambda _row: seen.append("intent"),
            screenshot_dir=tmp_path / "shots",
        )

    assert calls["save"] == 0
    assert seen == []


def test_confirm_each_accepts_enter(monkeypatch, tmp_path: Path) -> None:
    calls = _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        confirmation=FakeConfirmer(_confirmation(CONFIRMED)),
        confirm_each=True,
        input_func=lambda _prompt: "",
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert calls["save"] == 1


def test_a_failed_status_write_is_queued_and_reported(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_desktop(
        monkeypatch,
        evidence=SaveEvidence(unchanged=False, compared=("Invoice Number",)),
    )
    sidecar = tmp_path / "pending_status.json"

    result = run_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(failures=99),
        client=object(),
        confirmation=FakeConfirmer(_confirmation(CONFIRMED)),
        pending_status_path=sidecar,
        status_attempts=2,
        sleep=lambda _seconds: None,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert result.status_written is False
    queued = load_pending_status(sidecar)
    assert queued is not None
    assert queued.entries[0].row_number == 2
    assert queued.entries[0].status == "DONE"


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
    assert calls["save"] == 1


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
    assert result.stopped
    assert result.stopped_row == 2
    assert "stop_on_failure" in result.stop_reason


def test_run_rows_stops_when_a_status_could_not_be_written(
    monkeypatch,
) -> None:
    seen: list[int] = []

    def fake_run_row(row, **_kwargs):
        seen.append(row.row_number)
        return RowOutcome(
            row.row_number, "DONE", 1, status_written=False
        )

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
    )

    assert seen == [2]
    assert result.processed == 1
    assert result.stopped
    assert result.stopped_row == 2
    assert "status" in result.stop_reason
    assert "pending" in result.stop_reason


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


def test_run_rows_forwards_row_and_layout_callbacks(monkeypatch) -> None:
    rows_seen: list[RowOutcome] = []
    layouts_seen: list[object] = []
    layout = ResolvedLayout(fields={}, save=None)

    def fake_run_row(row, **kwargs):
        kwargs["on_layout"](layout, _window())
        return RowOutcome(row.row_number, "DONE", 1)

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    run_rows(
        [_row(2), _row(4)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        on_row=rows_seen.append,
        on_layout=lambda item, _window: layouts_seen.append(item),
    )

    assert [outcome.row_number for outcome in rows_seen] == [2, 4]
    assert layouts_seen == [layout, layout]


def test_run_rows_does_not_report_an_aborted_row(monkeypatch) -> None:
    rows_seen: list[RowOutcome] = []

    def fake_run_row(row, **_kwargs):
        if row.row_number == 4:
            raise RunnerAborted("stopped by the user")
        return RowOutcome(row.row_number, "DONE", 1)

    monkeypatch.setattr(row_runner, "run_row", fake_run_row)

    result = run_rows(
        [_row(2), _row(4)],
        config=_config(),
        mapping=_mapping(),
        writer=FakeWriter(),
        client=object(),
        on_row=rows_seen.append,
    )

    assert [outcome.row_number for outcome in rows_seen] == [2]
    assert result.stopped
    assert result.stopped_row == 4
