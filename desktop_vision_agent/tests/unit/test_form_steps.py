"""Unit tests for the static, step-driven form filler."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from excel_agent.agent import form_steps
from excel_agent.agent.calibration import ResolvedTarget, SaveEvidence
from excel_agent.agent.confirmation import (
    CONFIRMED,
    UNKNOWN,
    ConfirmationResult,
)
from excel_agent.agent.form_steps import (
    PlanResult,
    column_values,
    execute_form_steps,
    run_form_row,
    run_form_rows,
    step_columns,
)
from excel_agent.agent.row_runner import RunnerAborted, RunnerError
from excel_agent.config import AgentConfig, ConfigError
from excel_agent.desktop.capture import Capture
from excel_agent.desktop.input import InputError
from excel_agent.desktop.uia import UIAError, UIAInspection
from excel_agent.excel.reader import ExcelRow
from excel_agent.models.actions import ClickStep, FillStep
from excel_agent.config import ConfirmationConfig
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo


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


@dataclass
class FakeCandidate:
    candidate_id: str
    name: str
    control_type: str
    rect: Rect


class FakeInspection:
    """The slice of ``UIAInspection`` that resolution and read-back use."""

    def __init__(
        self,
        candidates: list[FakeCandidate],
        *,
        values: dict[str, str] | None = None,
        window: WindowInfo | None = None,
    ) -> None:
        self.candidates = tuple(candidates)
        self.window = window or _window()
        self._values = dict(values or {})
        self.reads: list[str] = []

    def read_value(self, candidate_id: str) -> str:
        self.reads.append(candidate_id)
        if candidate_id not in self._values:
            raise UIAError(f"no value for {candidate_id}")
        return self._values[candidate_id]


def _field(name: str, candidate_id: str = "e1") -> FakeCandidate:
    return FakeCandidate(
        candidate_id=candidate_id,
        name=name,
        control_type="Edit",
        rect=Rect(10, 20, 110, 40),
    )


def _button(name: str, candidate_id: str = "b1") -> FakeCandidate:
    return FakeCandidate(
        candidate_id=candidate_id,
        name=name,
        control_type="Button",
        rect=Rect(200, 200, 260, 240),
    )


def _config(
    *,
    steps: list[dict] | None = None,
    fields: list[dict] | None = None,
    max_attempts: int = 3,
    retry_on_failure: bool = False,
    stop_on_failure: bool = False,
) -> AgentConfig:
    payload: dict = {
        "app": {"title_contains": "Form"},
        "excel": {
            "input_path": "data/input/in.xlsx",
            "output_path": "data/output/out.xlsx",
            "sheet": "Rows",
            "id_column": "Invoice Number",
        },
        "fields": fields
        if fields is not None
        else [
            {"column": "Amount", "field": "Amount", "type": "number"},
            {"column": "Invoice Number", "field": "Invoice Number"},
        ],
        "actions": {"save_button": "Save", "wait_after_save_seconds": 0},
        "safety": {
            "max_attempts": max_attempts,
            "action_delay_seconds": 0,
            "stop_on_failure": stop_on_failure,
        },
    }
    if steps is not None:
        payload["form"] = {
            "steps": steps,
            "retry_on_failure": retry_on_failure,
            "pause_after_click_seconds": 0,
        }
    return AgentConfig.model_validate(payload)


def _row(values: dict | None = None, row_number: int = 2) -> ExcelRow:
    return ExcelRow(
        row_number=row_number,
        values=values
        if values is not None
        else {"Invoice Number": "INV-0001", "Amount": "500"},
    )


def _capture() -> Capture:
    return Capture(
        image=Image.new("RGB", (400, 300), "white"),
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=_window(),
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
    def __init__(self, outcome: str = CONFIRMED) -> None:
        self.outcome = outcome
        self.hints: list[str] = []

    def confirm(self, image, *, weak_hint: str = "") -> ConfirmationResult:
        self.hints.append(weak_hint)
        return ConfirmationResult(
            outcome=self.outcome,
            reason="fake",
            evidence=(),
            source="fake",
        )


def _stub_input(monkeypatch) -> dict[str, list]:
    """Replace the desktop input layer and the clock in ``form_steps``."""
    calls: dict[str, list] = {"clicks": [], "pastes": [], "keys": []}
    monkeypatch.setattr(
        form_steps,
        "click_checked",
        lambda x, y, *_a, **_k: calls["clicks"].append((x, y)),
    )
    monkeypatch.setattr(
        form_steps, "paste_text", lambda text: calls["pastes"].append(text)
    )
    monkeypatch.setattr(
        form_steps, "press_chord", lambda *_a: calls["keys"].append("all")
    )
    monkeypatch.setattr(
        form_steps, "press_key", lambda vk: calls["keys"].append(vk)
    )
    monkeypatch.setattr(form_steps, "_wait", lambda *a, **k: True)
    return calls


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------


def test_a_click_is_followed_by_a_fresh_inspection(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    before = FakeInspection([_button("Add New")])
    after = FakeInspection([_field("Amount")], values={"e1": "500"})
    inspected: list[int] = []

    def fake_inspect(_window):
        inspected.append(1)
        return after

    monkeypatch.setattr(form_steps, "inspect_window", fake_inspect)

    plan = execute_form_steps(
        [
            ClickStep(action="click", button="Add New"),
            FillStep(action="fill", column="Amount"),
        ],
        {"Amount": "500"},
        window=_window(),
        inspection=before,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
    )

    assert plan.ok, plan.problems
    assert plan.steps_run == 2
    assert list(plan.fields) == ["Amount"]
    assert calls["pastes"] == ["500"]
    assert len(calls["clicks"]) == 2
    assert inspected == [1]


def test_a_fill_is_read_back_from_the_inspection_that_resolved_it(
    monkeypatch,
) -> None:
    _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
    )

    assert plan.ok, plan.problems
    assert inspection.reads == ["e1"]


def test_a_read_back_mismatch_is_a_problem(monkeypatch) -> None:
    _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "50"})

    plan = execute_form_steps(
        [
            FillStep(action="fill", column="Amount"),
            FillStep(action="fill", column="Invoice Number"),
        ],
        {"Amount": "500", "Invoice Number": "INV-1"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount", "Invoice Number": "Invoice Number"},
        types={"Amount": "number", "Invoice Number": "text"},
    )

    assert not plan.ok
    assert "read back '50'" in plan.problems[0]
    assert plan.steps_run == 1


def test_an_unreadable_field_is_a_warning_not_a_failure(monkeypatch) -> None:
    _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Invoice Number")])

    plan = execute_form_steps(
        [FillStep(action="fill", column="Invoice Number")],
        {"Invoice Number": "INV-1"},
        window=_window(),
        inspection=inspection,
        labels={"Invoice Number": "Invoice Number"},
        types={"Invoice Number": "text"},
    )

    assert plan.ok, plan.problems
    assert "could not read" in plan.warnings[0]


def test_a_missing_button_blocks_the_plan(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)

    plan = execute_form_steps(
        [
            ClickStep(action="click", button="Add New"),
            FillStep(action="fill", column="Amount"),
        ],
        {"Amount": "500"},
        window=_window(),
        inspection=FakeInspection([_field("Amount")]),
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
    )

    assert not plan.ok
    assert "click 'Add New'" in plan.problems[0]
    assert plan.steps_run == 0
    assert calls["pastes"] == []


def test_an_optional_step_is_skipped_and_the_plan_continues(
    monkeypatch,
) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})

    plan = execute_form_steps(
        [
            ClickStep(action="click", button="Add Line", optional=True),
            FillStep(action="fill", column="Amount"),
        ],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
    )

    assert plan.ok, plan.problems
    assert plan.steps_run == 1
    assert "skipped" in plan.warnings[0]
    assert calls["pastes"] == ["500"]


def test_a_step_can_override_the_field_label(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection(
        [_field("Item Amount")], values={"e1": "500"}
    )

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount", field="Item Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
    )

    assert plan.ok, plan.problems
    assert calls["clicks"] == [(60, 30)]


def test_an_empty_value_clears_the_field(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Invoice Number")], values={"e1": ""})

    plan = execute_form_steps(
        [FillStep(action="fill", column="Invoice Number")],
        {"Invoice Number": ""},
        window=_window(),
        inspection=inspection,
        labels={"Invoice Number": "Invoice Number"},
        types={"Invoice Number": "text"},
    )

    assert plan.ok, plan.problems
    assert calls["pastes"] == []
    assert 0x2E in calls["keys"]


def test_a_stopped_controller_aborts_before_typing(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)

    class Stopped:
        def check(self) -> bool:
            return False

    with pytest.raises(RunnerAborted):
        execute_form_steps(
            [FillStep(action="fill", column="Amount")],
            {"Amount": "500"},
            window=_window(),
            inspection=FakeInspection([_field("Amount")]),
            labels={"Amount": "Amount"},
            types={"Amount": "number"},
            controller=Stopped(),
        )
    assert calls["pastes"] == []


def test_an_undeclared_label_is_reported_not_typed(monkeypatch) -> None:
    _stub_input(monkeypatch)

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=FakeInspection([_field("Amount")]),
        labels={},
        types={"Amount": "number"},
    )

    assert not plan.ok
    assert "has no field label" in plan.problems[0]


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_steps_are_parsed_into_typed_actions() -> None:
    config = _config(
        steps=[
            {"action": "click", "button": "Add New"},
            {"action": "fill", "column": "Amount"},
        ]
    )

    assert config.form is not None
    assert isinstance(config.form.steps[0], ClickStep)
    assert isinstance(config.form.steps[1], FillStep)
    assert config.form.retry_on_failure is False


def test_a_config_without_steps_keeps_the_mapped_pipeline() -> None:
    assert _config().form is None


def test_steps_must_fill_declared_columns() -> None:
    with pytest.raises(Exception) as excinfo:
        _config(steps=[{"action": "fill", "column": "Total"}])
    assert "does not declare" in str(excinfo.value)


def test_steps_must_not_be_empty() -> None:
    with pytest.raises(Exception) as excinfo:
        _config(steps=[])
    assert "at least 1" in str(excinfo.value)


def test_an_unknown_action_is_rejected() -> None:
    with pytest.raises(Exception) as excinfo:
        _config(steps=[{"action": "select", "column": "Amount"}])
    assert "action" in str(excinfo.value)


def test_column_values_use_the_declared_types() -> None:
    config = _config(steps=[{"action": "fill", "column": "Amount"}])

    values = column_values(_row(), config, config.form)

    assert values == {"Amount": "500"}


def test_column_values_reject_a_bad_number() -> None:
    config = _config(steps=[{"action": "fill", "column": "Amount"}])

    with pytest.raises(ValueError):
        column_values(_row({"Amount": "not a number"}), config, config.form)


def test_step_columns_are_listed_once_in_order() -> None:
    config = _config(
        steps=[
            {"action": "fill", "column": "Invoice Number"},
            {"action": "click", "button": "Add Line"},
            {"action": "fill", "column": "Amount"},
            {"action": "fill", "column": "Invoice Number"},
        ]
    )

    assert step_columns(config.form) == ["Invoice Number", "Amount"]


# ---------------------------------------------------------------------------
# Row outcomes
# ---------------------------------------------------------------------------


def _stub_row(monkeypatch, plan: PlanResult, *, save=True) -> dict[str, int]:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection(
        [_field("Amount"), _button("Save", "b9")] if save else [_field("Amount")],
        values={"e1": "500"},
    )
    calls["saves"] = []

    monkeypatch.setattr(form_steps, "attach_window", lambda _app: _window())
    monkeypatch.setattr(form_steps, "inspect_window", lambda _w, **_k: inspection)
    monkeypatch.setattr(form_steps, "execute_form_steps", lambda *a, **k: plan)
    monkeypatch.setattr(form_steps, "capture_window", lambda _w: _capture())
    monkeypatch.setattr(
        form_steps,
        "compare_saved_fields",
        lambda *a, **k: SaveEvidence(unchanged=False, detail="fields cleared"),
    )
    monkeypatch.setattr(
        form_steps,
        "click_checked",
        lambda x, y, *_a, **_k: calls["saves"].append((x, y)),
    )
    return calls


def _plan(inspection: FakeInspection | None = None, **kwargs) -> PlanResult:
    return PlanResult(
        inspection=inspection
        if inspection is not None
        else FakeInspection([_field("Amount"), _button("Save", "b9")]),
        fields=kwargs.pop(
            "fields",
            {
                "Amount": ResolvedTarget(
                    key="Amount",
                    rect=Rect(10, 20, 110, 40),
                    candidate_id="e1",
                    source="uia-name",
                )
            },
        ),
        steps_run=kwargs.pop("steps_run", 1),
        **kwargs,
    )


def _form_config(**kwargs) -> AgentConfig:
    return _config(
        steps=[
            {"action": "click", "button": "Add New"},
            {"action": "fill", "column": "Amount"},
        ],
        **kwargs,
    )


def test_a_confirmed_save_marks_the_row_done(monkeypatch) -> None:
    calls = _stub_row(monkeypatch, _plan())
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "DONE"
    assert writer.calls == [(2, "DONE", "")]
    assert len(calls["saves"]) == 1


def test_a_failed_plan_is_a_recheck_and_is_not_retried(monkeypatch) -> None:
    inspection = FakeInspection([_button("Save", "b9")])
    plan = PlanResult(
        inspection=inspection,
        fields={},
        problems=("step 1 (click 'Add New'): no match",),
    )
    calls = _stub_row(monkeypatch, plan)
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(max_attempts=3),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert outcome.attempts == 1
    assert "step plan failed before Save" in outcome.error
    assert calls["saves"] == []
    assert writer.calls[0][1] == "RECHECK"


def test_an_unconfirmed_save_is_a_recheck_and_is_never_saved_twice(
    monkeypatch,
) -> None:
    calls = _stub_row(monkeypatch, _plan())
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(max_attempts=3),
        writer=writer,
        confirmation=FakeConfirmer(outcome=UNKNOWN),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert "may already exist" in outcome.error
    assert len(calls["saves"]) == 1


def test_the_save_button_must_be_present_in_the_live_window(
    monkeypatch,
) -> None:
    calls = _stub_row(
        monkeypatch, _plan(FakeInspection([_field("Amount")])), save=False
    )
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert "Save button" in outcome.error
    assert calls["saves"] == []


def test_an_invalid_number_is_a_recheck_without_typing(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    writer = FakeWriter()

    outcome = run_form_row(
        _row({"Amount": "abc"}),
        config=_form_config(),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert outcome.attempts == 0
    assert calls["clicks"] == []


def test_the_plan_is_retried_only_when_configured(monkeypatch) -> None:
    plan = PlanResult(
        inspection=FakeInspection([_button("Save", "b9")]),
        fields={},
        problems=("step 2 (fill 'Amount'): no match",),
    )
    _stub_row(monkeypatch, plan)
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(max_attempts=2, retry_on_failure=True),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert outcome.attempts == 2


# ---------------------------------------------------------------------------
# Pause and resume
# ---------------------------------------------------------------------------


class _ResumingController:
    """A controller that reports a pause-and-resume on a given check."""

    def __init__(self, resume_on: int) -> None:
        self._resume_on = resume_on
        self.pause_count = 0
        self.checks = 0

    def check(self) -> bool:
        self.checks += 1
        if self.checks >= self._resume_on:
            self.pause_count = 1
        return True


def test_a_resume_brings_the_window_back_and_reads_the_screen_again(
    monkeypatch,
) -> None:
    calls = _stub_input(monkeypatch)
    before = FakeInspection([_field("Amount")], values={"e1": "700"})
    after = FakeInspection([_field("Amount")], values={"e1": "700"})
    inspected: list[int] = []
    focused: list[object] = []
    notes: list[str] = []

    def fake_inspect(_window, **_kwargs):
        inspected.append(1)
        return after

    monkeypatch.setattr(form_steps, "inspect_window", fake_inspect)
    monkeypatch.setattr(
        form_steps, "focus_window", lambda window: focused.append(window) or True
    )
    controller = _ResumingController(resume_on=3)

    plan = execute_form_steps(
        [
            FillStep(action="fill", column="Amount"),
            FillStep(action="fill", column="Amount"),
        ],
        {"Amount": "700"},
        window=_window(),
        inspection=before,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
        controller=controller,
        sleep=lambda _s: None,
        output=notes.append,
    )

    assert plan.ok, plan.problems
    assert calls["pastes"] == ["700", "700"]
    # The window was brought back once, and the UI was read again afterwards.
    assert len(focused) == 1
    assert inspected == [1]
    assert "  resumed: the window was brought back to the front" in notes


def test_a_pause_before_the_paste_clicks_the_field_again(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})
    focused: list[object] = []

    monkeypatch.setattr(
        form_steps, "focus_window", lambda window: focused.append(window) or True
    )
    controller = _ResumingController(resume_on=2)

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
        controller=controller,
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert plan.ok, plan.problems
    # Clicked to focus the field, then clicked it again after the resume: the
    # pause may have moved the focus, and the paste must not go elsewhere.
    assert len(calls["clicks"]) == 2
    assert calls["pastes"] == ["500"]
    assert len(focused) == 1


def test_a_resume_that_cannot_raise_the_window_stops_the_run(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})
    monkeypatch.setattr(form_steps, "focus_window", lambda _window: False)
    controller = _ResumingController(resume_on=3)

    with pytest.raises(RunnerAborted) as excinfo:
        execute_form_steps(
            [
                FillStep(action="fill", column="Amount"),
                FillStep(action="fill", column="Amount"),
            ],
            {"Amount": "500"},
            window=_window(),
            inspection=inspection,
            labels={"Amount": "Amount"},
            types={"Amount": "number"},
            controller=controller,
            sleep=lambda _s: None,
            output=lambda _m: None,
        )

    # Failing open would click into whatever is in front; it must not continue.
    assert "could not be brought back to the front" in str(excinfo.value)
    assert len(calls["pastes"]) == 1


def test_a_paste_that_lands_after_the_first_read_is_not_a_failure(
    monkeypatch,
) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})
    reads = iter(["", "500"])
    monkeypatch.setattr(inspection, "read_value", lambda _cid: next(reads))

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
        sleep=lambda _s: None,
    )

    assert plan.ok, plan.problems
    assert calls["pastes"] == ["500"]


def test_a_field_that_still_reads_wrong_after_the_second_look_fails(
    monkeypatch,
) -> None:
    _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": ""})

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
        sleep=lambda _s: None,
    )

    assert not plan.ok
    assert "read back ''" in plan.problems[0]
    # Read once immediately and once more after the settle.
    assert len(inspection.reads) == 2


def test_a_refused_click_stops_the_step_without_typing(monkeypatch) -> None:
    calls = _stub_input(monkeypatch)
    inspection = FakeInspection([_field("Amount")], values={"e1": "500"})

    def refuse(*_args, **_kwargs):
        raise InputError("nothing was clicked: the window moved")

    monkeypatch.setattr(form_steps, "click_checked", refuse)

    plan = execute_form_steps(
        [FillStep(action="fill", column="Amount")],
        {"Amount": "500"},
        window=_window(),
        inspection=inspection,
        labels={"Amount": "Amount"},
        types={"Amount": "number"},
        sleep=lambda _s: None,
    )

    assert not plan.ok
    assert "nothing was clicked" in plan.problems[0]
    assert calls["pastes"] == []


def test_a_pause_before_save_finds_the_button_again_instead_of_clicking(
    monkeypatch,
) -> None:
    calls = _stub_row(monkeypatch, _plan())
    # After the pause the form is gone: only the table is left on the screen.
    monkeypatch.setattr(
        form_steps, "inspect_window", lambda _w, **_k: FakeInspection([])
    )
    focused: list[object] = []
    monkeypatch.setattr(
        form_steps, "focus_window", lambda window: focused.append(window) or True
    )
    controller = _ResumingController(resume_on=2)
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=_form_config(),
        writer=writer,
        confirmation=FakeConfirmer(),
        controller=controller,
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert "while the run was paused" in outcome.error
    assert calls["saves"] == []
    assert len(focused) == 1


def test_run_form_rows_rejects_a_row_without_a_step_column() -> None:
    with pytest.raises(RunnerError) as excinfo:
        run_form_rows(
            [_row({"Invoice Number": "INV-1"})],
            config=_form_config(),
            writer=FakeWriter(),
            confirmation=FakeConfirmer(),
            output=lambda _m: None,
        )
    assert "missing from the workbook" in str(excinfo.value)


def test_run_form_rows_requires_a_plan() -> None:
    with pytest.raises(RunnerError):
        run_form_rows(
            [_row()],
            config=_config(),
            writer=FakeWriter(),
            confirmation=FakeConfirmer(),
            output=lambda _m: None,
        )


def test_run_form_rows_stops_after_a_recheck_when_configured(
    monkeypatch,
) -> None:
    _stub_row(
        monkeypatch,
        PlanResult(
            inspection=FakeInspection([_button("Save", "b9")]),
            fields={},
            problems=("boom",),
        ),
    )
    writer = FakeWriter()

    result = run_form_rows(
        [
            _row(row_number=2),
            _row(row_number=3),
            _row(row_number=4),
        ],
        config=_form_config(stop_on_failure=True),
        writer=writer,
        confirmation=FakeConfirmer(),
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert result.stopped is True
    assert result.stopped_row == 2
    assert len(result.outcomes) == 1


def test_a_failed_status_write_is_queued_and_stops_the_batch(
    monkeypatch, tmp_path
) -> None:
    _stub_row(monkeypatch, _plan())
    writer = FakeWriter(failures=5)
    sidecar = tmp_path / "pending_status.json"

    result = run_form_rows(
        [_row(row_number=2), _row(row_number=3)],
        config=_form_config(),
        writer=writer,
        confirmation=FakeConfirmer(),
        pending_status_path=sidecar,
        status_attempts=1,
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert result.stopped is True
    assert result.outcomes[0].status_written is False
    assert sidecar.is_file()

def _uia(*items: tuple[str, str, str]) -> UIAInspection:
    candidates = tuple(
        UIACandidate(
            candidate_id=f"W{index}",
            name=name,
            control_type=control_type,
            automation_id="",
            class_name="",
            rect=Rect(10, 20 * index, 110, 20 * index + 18),
            value=value,
            enabled=True,
        )
        for index, (name, control_type, value) in enumerate(items, start=1)
    )
    return UIAInspection(window=_window(), candidates=candidates, _controls={})


def test_a_save_is_confirmed_from_the_window_text_without_ai(monkeypatch) -> None:
    """verifier="row" needs no confirmer at all: the new value is the evidence."""
    plan = _plan(inspection=_uia(("Amount", "Edit", "500"), ("Save", "Button", "")))
    _stub_row(monkeypatch, plan)
    after = _uia(("500", "Text", ""))
    reads = {"count": 0}

    def fake_inspect(_window, **_kwargs):
        # The attempt reads the window, then the pre-Save evidence, then the
        # screen after the click.
        reads["count"] += 1
        return after if reads["count"] > 2 else plan.inspection

    monkeypatch.setattr(form_steps, "inspect_window", fake_inspect)
    messages: list[str] = []
    config = _form_config().model_copy(
        update={
            "confirmation": ConfirmationConfig(
                verifier="row", footprint=["Amount"]
            )
        }
    )
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=config,
        writer=writer,
        sleep=lambda _s: None,
        output=messages.append,
    )

    assert outcome.status == "DONE"
    assert writer.calls == [(2, "DONE", "")]
    assert any("the row appeared" in message for message in messages)


def test_a_local_check_without_the_value_is_a_recheck(monkeypatch) -> None:
    plan = _plan(inspection=_uia(("Amount", "Edit", "500"), ("Save", "Button", "")))
    _stub_row(monkeypatch, plan)
    monkeypatch.setattr(
        form_steps,
        "inspect_window",
        lambda _w, **_k: _uia(("Clients", "Text", "")),
    )
    config = _form_config().model_copy(
        update={
            "confirmation": ConfirmationConfig(
                verifier="row", footprint=["Amount"]
            )
        }
    )
    writer = FakeWriter()

    outcome = run_form_row(
        _row(),
        config=config,
        writer=writer,
        sleep=lambda _s: None,
        output=lambda _m: None,
    )

    assert outcome.status == "RECHECK"
    assert "none of the saved values appeared" in outcome.error
