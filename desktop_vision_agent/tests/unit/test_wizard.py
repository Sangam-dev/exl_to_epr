"""Unit tests for the interactive agent wizard (python main.py --agent)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from excel_agent.agent.wizard import (
    SaveOutcome,
    _save_live_row,
    _ask_save_button,
    _clean_path,
    _describe_step,
    _form_steps,
    _generate_config,
    _infer_column_type,
    _preview_config,
    _prompt,
    _prompt_yes_no,
    _review_step_plan,
    _select_mode,
    run_wizard,
)
from excel_agent.ai.step_planner import PlannedFill, StepPlan, StepUnsupported
from excel_agent.agent.calibration import ResolvedTarget
from excel_agent.config import AgentConfig, load_config
from excel_agent.excel.reader import ExcelRow
from excel_agent.desktop.uia import UIAInspection
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo


def test_clean_path_removes_quotes_and_whitespace() -> None:
    assert _clean_path('  "C:/data/invoices.xlsx"  ') == "C:/data/invoices.xlsx"
    assert _clean_path("  'C:/data/invoices.xlsx'  ") == "C:/data/invoices.xlsx"
    assert _clean_path("C:/data/invoices.xlsx") == "C:/data/invoices.xlsx"


def test_prompt_uses_default_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt("Enter value", default="default_val") == "default_val"


def test_prompt_returns_user_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "my_value")
    assert _prompt("Enter value", default="default_val") == "my_value"


def test_prompt_yes_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _prompt_yes_no("Proceed?") is True

    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert _prompt_yes_no("Proceed?") is False

    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes_no("Proceed?", default_yes=True) is True
    assert _prompt_yes_no("Proceed?", default_yes=False) is False


def test_generate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)

    config_path = _generate_config(
        app_title="Test App",
        process_name="app.exe",
        excel_path="C:/data/test.xlsx",
        sheet_name="Invoices",
        id_column="InvoiceID",
        headers=["InvoiceID", "Customer", "Amount"],
        save_button="Submit",
    )

    assert config_path.is_file()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["app"]["title_contains"] == "Test App"
    assert data["app"]["process_name"] == "app.exe"
    assert data["excel"]["sheet"] == "Invoices"
    assert data["excel"]["id_column"] == "InvoiceID"
    assert data["actions"]["save_button"] == "Submit"


def test_run_wizard_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_window",
        lambda: ("Test App", "app.exe"),
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_excel",
        lambda: ("C:/data/test.xlsx", "Sheet1", "ID", ["ID", "Name"]),
    )
    monkeypatch.setattr("excel_agent.agent.wizard._prompt", lambda prompt, default="": default or "Save")
    monkeypatch.setattr("excel_agent.agent.wizard._prompt_yes_no", lambda q, default_yes=True: True)

    mock_setup = MagicMock(return_value=0)
    mock_calibrate = MagicMock(return_value=0)
    mock_batch = MagicMock(return_value=0)

    monkeypatch.setattr("excel_agent.cli.run_setup", mock_setup)
    monkeypatch.setattr("excel_agent.cli.run_calibrate", mock_calibrate)
    monkeypatch.setattr("excel_agent.cli.run_batch", mock_batch)

    # Fast forward time.sleep in wizard
    monkeypatch.setattr("time.sleep", lambda _: None)

    exit_code = run_wizard()
    assert exit_code == 0
    assert mock_setup.called
    assert mock_calibrate.called
    assert mock_batch.called


# ---------------------------------------------------------------------------
# Step-plan mode
# ---------------------------------------------------------------------------


def _scripted_prompt(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    queue = list(answers)

    def fake_prompt(text: str, default: str | None = None) -> str:
        if not queue:
            return default if default is not None else ""
        return queue.pop(0)

    monkeypatch.setattr("excel_agent.agent.wizard._prompt", fake_prompt)


def test_select_mode_defaults_to_the_mapping_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scripted_prompt(monkeypatch, [""])
    assert _select_mode() == "mapping"


def test_select_mode_accepts_the_steps_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scripted_prompt(monkeypatch, ["2"])
    assert _select_mode() == "steps"

    _scripted_prompt(monkeypatch, ["steps"])
    assert _select_mode() == "steps"


def test_describe_step_names_the_work() -> None:
    assert _describe_step({"action": "click", "button": "Add New"}) == (
        "click 'Add New'"
    )
    assert _describe_step(
        {"action": "fill", "column": "Amount", "field": "Total"}
    ) == "fill 'Total' from column 'Amount'"


def test_form_steps_turn_a_click_plan_into_a_click_step() -> None:
    steps = _form_steps(StepPlan(kind="click", label="Add New"))

    assert len(steps) == 1
    assert steps[0].action == "click"
    assert steps[0].button == "Add New"


def test_form_steps_turn_a_fill_plan_into_one_step_per_field() -> None:
    plan = StepPlan(
        kind="fill",
        fills=(
            PlannedFill(column="Invoice Number", label="Invoice No."),
            PlannedFill(column="Amount", label="Total"),
        ),
    )

    steps = _form_steps(plan)

    assert [(step.action, step.column, step.field) for step in steps] == [
        ("fill", "Invoice Number", "Invoice No."),
        ("fill", "Amount", "Total"),
    ]


def test_the_approval_screen_lists_the_actions_and_the_leftovers(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from excel_agent.agent.wizard import _print_step_plan

    plan = StepPlan(
        kind="fill",
        fills=(
            PlannedFill(column="Amount", label="Total", confidence=0.88),
        ),
    )

    _print_step_plan(
        plan,
        index=1,
        instruction="fill the amount",
        screenshot=tmp_path / "step-01-before.png",
        headers=["Amount", "Notes", "Agent Status"],
        types={"Amount": "number"},
    )

    out = capsys.readouterr().out
    assert "1. fill 'Total' <- column 'Amount' (number, 0.88)" in out
    assert "Not filled by this step: Notes" in out
    assert "Agent Status" not in out


def test_review_accepts_the_step_on_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scripted_prompt(monkeypatch, [""])
    assert _review_step_plan() == ("done", "")

    _scripted_prompt(monkeypatch, ["done"])
    assert _review_step_plan() == ("done", "")


def test_review_redoes_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_prompt(monkeypatch, ["r"])
    assert _review_step_plan() == ("redo", "")


def test_review_takes_a_correction_in_plain_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scripted_prompt(monkeypatch, ["the amount goes in Total, not Country"])

    action, correction = _review_step_plan()

    assert action == "edit"
    assert correction == "the amount goes in Total, not Country"


def test_review_of_a_failed_step_defaults_to_redo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scripted_prompt(monkeypatch, [""])

    action, correction = _review_step_plan("no text field on this screen is named 'Total'")

    assert action == "redo"
    assert correction == ""


def test_preview_config_reads_the_workbook_before_steps_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)

    config = _preview_config("App", "app.exe", "C:/data/test.xlsx", "Sheet1", "ID")

    assert config.excel.input_path == "C:/data/test.xlsx"
    assert config.excel.id_column == "ID"
    assert config.form is None
    assert config.fields == []


def test_generate_config_writes_a_valid_step_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)

    config_path = _generate_config(
        app_title="App",
        process_name=None,
        excel_path=str(tmp_path / "in.xlsx"),
        sheet_name="Sheet1",
        id_column="ID",
        headers=["ID", "Amount"],
        save_button="Save",
        form={"steps": [{"action": "fill", "column": "Amount"}]},
        fields=[{"column": "Amount", "field": "Amount", "type": "number"}],
        confirmation={
            "success_region": None,
            "error_region": None,
            "success_pattern": "(?i)saved",
            "error_patterns": ["(?i)error"],
        },
        filename="agent.steps.json",
    )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["form"]["steps"] == [{"action": "fill", "column": "Amount"}]
    assert data["fields"] == [
        {"column": "Amount", "field": "Amount", "type": "number"}
    ]
    assert data["confirmation"]["success_pattern"] == "(?i)saved"
    assert load_config(str(config_path)).form is not None


def test_infer_column_type_reads_the_workbook(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "data.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Rows"
    ws.append(["ID", "Amount", "Note"])
    ws.append([1, 1250, "hello"])
    ws.append([2, 99.5, "world"])
    wb.save(path)

    assert _infer_column_type(str(path), "Rows", "Amount") == "number"
    assert _infer_column_type(str(path), "Rows", "Note") == "text"
    assert _infer_column_type(str(path), "Rows", "Missing") == "text"


def test_an_empty_column_is_text_not_a_number(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "empty.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Amount"])
    ws.append([1, None])
    wb.save(path)

    assert _infer_column_type(str(path), "Sheet", "Amount") == "text"


# ---------------------------------------------------------------------------
# Multi-step mode: describe each step, watch it run on the first row
# ---------------------------------------------------------------------------


def _fake_capture() -> MagicMock:
    capture = MagicMock()
    capture.image = Image.new("RGB", (8, 8), "white")
    return capture


def _drive_multi_step(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    answers: list[str],
    plans: list[StepPlan],
    unsupported_once: str | None = None,
    inspect_sequence: list[Any] | None = None,
    seen_candidates: list[list[Any]] | None = None,
) -> dict[str, MagicMock]:
    """Run the wizard in multi-step mode with the screen and AI faked."""
    monkeypatch.setattr("excel_agent.agent.wizard.project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_window", lambda: ("Test App", "app.exe")
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard._select_excel",
        lambda: ("C:/data/test.xlsx", "Sheet1", "ID", ["ID", "Name"]),
    )
    monkeypatch.setattr("excel_agent.agent.wizard._select_mode", lambda: "steps")
    monkeypatch.setattr(
        "excel_agent.agent.wizard._inspect_live",
        lambda _title: (MagicMock(), MagicMock(candidates=())),
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard.read_workbook", lambda *_a, **_k: MagicMock()
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard.pending_rows",
        lambda *_a, **_k: [ExcelRow(row_number=2, values={"ID": 1, "Name": "Ada"})],
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard._infer_column_type", lambda *_a, **_k: "text"
    )
    monkeypatch.setattr(
        "excel_agent.desktop.capture.capture_window", lambda _window: _fake_capture()
    )
    monkeypatch.setattr(
        "excel_agent.ai.client.GeminiClient", lambda *_a, **_k: MagicMock()
    )
    queue = list(plans)
    screens = list(inspect_sequence or [])
    unsupported_left = 1 if unsupported_once is not None else 0

    def fake_inspect(_window: object, **_kwargs: object) -> object:
        if len(screens) > 1:
            return screens.pop(0)
        return screens[0] if screens else MagicMock()

    def fake_plan(
        _client: object,
        _capture: object,
        candidates: tuple[Any, ...],
        _headers: list[str],
        instruction: str,
        *_a: object,
        **_k: object,
    ) -> StepPlan:
        nonlocal unsupported_left
        if seen_candidates is not None:
            seen_candidates.append(list(candidates))
        if instruction == unsupported_once and unsupported_left:
            unsupported_left -= 1
            raise StepUnsupported("the fields you asked for are not here yet")
        return queue.pop(0)

    monkeypatch.setattr("excel_agent.desktop.uia.inspect_window", fake_inspect)
    monkeypatch.setattr("excel_agent.ai.step_planner.plan_step", fake_plan)
    monkeypatch.setattr(
        "excel_agent.agent.form_steps.execute_form_steps",
        lambda *_a, **_k: SimpleNamespace(
            fields={"Name": MagicMock()}, warnings=(), problems=()
        ),
    )
    monkeypatch.setattr(
        "excel_agent.agent.wizard._ask_confirmation",
        lambda: {
            "success_region": None,
            "error_region": None,
            "success_pattern": "(?i)saved",
            "error_patterns": [],
        },
    )
    save = MagicMock(return_value=SaveOutcome(status="DONE"))
    monkeypatch.setattr("excel_agent.agent.wizard._save_live_row", save)
    run_form = MagicMock(return_value=0)
    monkeypatch.setattr("excel_agent.cli.run_form", run_form)
    monkeypatch.setattr(
        "excel_agent.agent.wizard._prompt_yes_no", lambda *_a, **_k: True
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    _scripted_prompt(monkeypatch, answers)

    assert run_wizard() == 0
    return {"run_form": run_form, "save": save}


def test_multi_step_wizard_saves_row_one_then_runs_the_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=[
            "1", "click Add New", "",      # add a click step, approve it
            "1", "fill the name", "",      # add a fill step, approve it
            "2",                            # start automation
            "",                             # Save button (default)
            "",                             # all remaining rows
        ],
        plans=[
            StepPlan(kind="click", label="Add New", confidence=0.9),
            StepPlan(
                kind="fill",
                fills=(PlannedFill(column="Name", label="Full Name", confidence=0.9),),
                confidence=0.9,
            ),
        ],
    )

    assert calls["save"].call_args.kwargs["row_number"] == 2
    assert calls["save"].call_args.kwargs["values"] == {"Name": "Ada"}

    run_form = calls["run_form"]
    assert run_form.called
    config_path = Path(run_form.call_args.args[0])
    assert config_path.name == "agent.interactive.steps.json"
    assert run_form.call_args.kwargs["limit"] is None
    assert run_form.call_args.kwargs["confirm_each"] is True

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["form"]["steps"] == [
        {"action": "click", "button": "Add New"},
        {"action": "fill", "column": "Name", "field": "Full Name"},
    ]
    assert data["fields"] == [
        {"column": "Name", "field": "Full Name", "type": "text"}
    ]
    load_config(str(config_path))


def test_start_automation_needs_a_step_that_fills_something(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=[
            "1", "click Add New", "",   # only a click so far
            "2",                         # refused: nothing fills a column
            "1", "fill the name", "",   # add the fill step
            "2",                         # now start
            "",
            "",
        ],
        plans=[
            StepPlan(kind="click", label="Add New"),
            StepPlan(
                kind="fill",
                fills=(PlannedFill(column="Name", label="Full Name"),),
            ),
        ],
    )

    assert "No step fills anything yet" in capsys.readouterr().out


def _button(name: str) -> UIACandidate:
    return UIACandidate(
        candidate_id=f"W{len(name)}",
        name=name,
        control_type="Button",
        automation_id="",
        class_name="",
        rect=Rect(0, 0, 10, 10),
        value="",
        enabled=True,
    )


def _button_inspection(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        candidates=tuple(_button(name) for name in names), window=MagicMock()
    )


def test_the_only_save_button_becomes_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "excel_agent.desktop.uia.inspect_window",
        lambda _window: _button_inspection("Minimise", "+ Add", "Save client"),
    )
    _scripted_prompt(monkeypatch, [""])

    assert _ask_save_button(MagicMock(), MagicMock()) == "Save client"


def test_a_save_button_that_is_not_on_screen_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "excel_agent.desktop.uia.inspect_window",
        lambda _window: _button_inspection("Add", "Commit"),
    )
    _scripted_prompt(monkeypatch, ["Save", "Commit"])

    assert _ask_save_button(MagicMock(), MagicMock()) == "Commit"
    assert "No button named 'Save' is on this screen." in capsys.readouterr().out


def test_a_step_sees_the_controls_the_previous_click_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A click can open a dialog, so the next step must read the screen again."""
    before = _button_inspection("+ Add")
    after = _button_inspection("+ Add", "Client ID", "Save client")
    seen: list[list] = []

    _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=[
            "1", "click + Add", "",      # add the click step, approve it
            "1", "fill Client ID", "",   # add the fill step, approve it
            "2",                         # start automation
            "",                          # Save button (default)
            "",                          # all remaining rows
        ],
        plans=[
            StepPlan(kind="click", label="+ Add", confidence=0.9),
            StepPlan(
                kind="fill",
                fills=(PlannedFill(column="Name", label="Client ID", confidence=0.9),),
            ),
        ],
        inspect_sequence=[before, before, after],
        seen_candidates=seen,
    )

    assert seen[0] == list(before.candidates)
    assert seen[1] == list(after.candidates)


def test_an_unsupported_step_can_be_retried_without_losing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=[
            "1", "click Add New", "",    # add a click step, approve it
            "1", "fill the name",        # the planner cannot do that yet
            "",                          # Enter on the problem re-reads the screen
            "",                          # approve the step on the second look
            "2", "", "",
        ],
        plans=[
            StepPlan(kind="click", label="Add New", confidence=0.9),
            StepPlan(
                kind="fill",
                fills=(PlannedFill(column="Name", label="Full Name", confidence=0.9),),
            ),
        ],
        unsupported_once="fill the name",
    )

    out = capsys.readouterr().out
    assert "Not supported yet: the fields you asked for are not here yet" in out
    assert "Clicking buttons and typing into text fields are the only" in out

def _happy_path_answers() -> list[str]:
    return ["1", "click Add New", "", "1", "fill the name", "", "2", "", ""]


def _happy_path_plans() -> list[StepPlan]:
    return [
        StepPlan(kind="click", label="Add New", confidence=0.9),
        StepPlan(
            kind="fill",
            fills=(PlannedFill(column="Name", label="Full Name", confidence=0.9),),
        ),
    ]


def test_the_save_check_question_is_asked_before_row_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=_happy_path_answers(),
        plans=_happy_path_plans(),
    )

    out = capsys.readouterr().out
    assert "Check every Save with AI? Gemini reads the screen after each Save." in out
    assert "[y] AI reads each screen" in out
    assert "[n] check the new row locally (no AI, no API cost)" in out
    assert "Save check: AI reads each screen" in out


def test_saying_no_switches_later_saves_to_the_local_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard._ask_save_check", lambda: "row")

    calls = _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=_happy_path_answers(),
        plans=_happy_path_plans(),
    )

    config_path = Path(calls["run_form"].call_args.args[0])
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["confirmation"]["verifier"] == "row"
    # No screen evidence in this fake run, so the ID column is the fallback.
    assert data["confirmation"]["footprint"] == ["ID"]
    assert "Save check: local window text" in capsys.readouterr().out


def test_saying_no_to_the_check_itself_runs_without_any_screen_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("excel_agent.agent.wizard._ask_save_check", lambda: "none")

    calls = _drive_multi_step(
        monkeypatch,
        tmp_path,
        answers=_happy_path_answers(),
        plans=_happy_path_plans(),
    )

    config_path = Path(calls["run_form"].call_args.args[0])
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["confirmation"]["verifier"] == "none"
    out = capsys.readouterr().out
    assert "Save check: none - every Save counts as done" in out
    # Nothing is looked for, so no value has to be guessed for later rows.
    assert "Save evidence" not in out
    assert "to look for" not in out

# ---------------------------------------------------------------------------
# The first Save is judged on the row, not only on a success message
# ---------------------------------------------------------------------------


def _evidence_candidate(
    name: str, *, control_type: str = "Text", value: str = ""
) -> UIACandidate:
    return UIACandidate(
        candidate_id=f"W{abs(hash((name, control_type, value))) % 10000}",
        name=name,
        control_type=control_type,
        automation_id="",
        class_name="",
        rect=Rect(0, 0, 10, 10),
        value=value,
        enabled=True,
    )


def _evidence_inspection(*candidates: UIACandidate) -> UIAInspection:
    window = WindowInfo(
        handle=7,
        title="Clients - Brave",
        process_name="brave.exe",
        class_name="Chrome_WidgetWin_1",
        rect=Rect(0, 0, 1200, 800),
        client_rect=Rect(0, 0, 1200, 800),
        dpi=96,
    )
    return UIAInspection(window=window, candidates=candidates, _controls={})


def _first_row_config(scrolling: bool = True) -> Any:
    return AgentConfig.model_validate(
        {
            "scrolling": {"enabled": scrolling},
            "app": {"title_contains": "Clients"},
            "excel": {
                "input_path": "data/input/in.xlsx",
                "output_path": "data/output/out.xlsx",
                "sheet": "Client Entries",
                "id_column": "Client ID",
            },
            "fields": [{"column": "Client ID", "field": "Client ID"}],
            "actions": {"save_button": "Save client", "wait_after_save_seconds": 0},
            "safety": {"action_delay_seconds": 0},
        }
    )


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []
        self.closed = False

    def write_status(self, row_number: int, status: str, error: str = "") -> None:
        self.calls.append((row_number, status, error))

    def close(self) -> None:
        self.closed = True


def test_the_first_save_is_judged_on_the_row_when_no_success_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The app that shows no message must not lose its first row to RECHECK.

    This is the run that skipped row 2: the wizard saved it, had no success
    pattern to match, and wrote RECHECK - which the batch then skipped.
    """
    before = _evidence_inspection(
        _evidence_candidate("Client ID", control_type="Edit", value="CL-1001"),
        _evidence_candidate("Clients registered"),
    )
    after = _evidence_inspection(
        _evidence_candidate("Clients registered"),
        _evidence_candidate("CL-1001 CL-1001", control_type="DataItem"),
    )
    reads = iter([before, after])
    monkeypatch.setattr(
        "excel_agent.desktop.uia.inspect_window", lambda *_a, **_k: next(reads)
    )
    monkeypatch.setattr(
        "excel_agent.desktop.capture.capture_window",
        lambda _w: SimpleNamespace(image=Image.new("RGB", (8, 8), "white")),
    )
    monkeypatch.setattr("excel_agent.desktop.input.click", lambda *_a, **_k: None)
    save = ResolvedTarget(
        key="Save client",
        rect=Rect(4, 4, 8, 8),
        candidate_id="b1",
        source="uia-name",
    )
    monkeypatch.setattr(
        "excel_agent.agent.calibration.resolve_page_target",
        lambda *_a, **_k: (save, None),
    )
    monkeypatch.setattr(
        "excel_agent.agent.calibration.compare_saved_fields",
        lambda *_a, **_k: SimpleNamespace(detail="fields cleared"),
    )
    monkeypatch.setattr(
        "excel_agent.agent.workspace.open_workspace",
        lambda _config: SimpleNamespace(path=tmp_path / "workbook.xlsx"),
    )
    writer = _RecordingWriter()
    monkeypatch.setattr(
        "excel_agent.excel.writer.WorkbookWriter.from_config",
        classmethod(lambda _cls, _config, path=None: writer),
    )
    def write_through(write, *args, **_kwargs):
        write(*args)
        return True, ""

    monkeypatch.setattr(
        "excel_agent.agent.pending_status.write_status_with_retries",
        write_through,
    )
    client = MagicMock()
    monkeypatch.setattr("excel_agent.ai.client.GeminiClient", client)

    outcome = _save_live_row(
        config=_first_row_config(),
        row_number=2,
        values={
            "Client ID": "CL-1001",
            "Client / Company Name": "Himalayan Traders",
        },
        fields={"Client ID": save},
        window=before.window,
        session=tmp_path,
    )

    assert outcome.status == "DONE"
    assert writer.calls == [(2, "DONE", "")]
    assert client.call_count == 0
    assert "the row appeared" in capsys.readouterr().out


def test_the_first_save_without_any_evidence_is_still_a_recheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No success text and no new row on screen: RECHECK, never a guess."""
    empty = _evidence_inspection(_evidence_candidate("Clients registered"))
    reads = iter([empty, empty])
    monkeypatch.setattr(
        "excel_agent.desktop.uia.inspect_window", lambda *_a, **_k: next(reads)
    )
    monkeypatch.setattr(
        "excel_agent.desktop.capture.capture_window",
        lambda _w: SimpleNamespace(image=Image.new("RGB", (8, 8), "white")),
    )
    monkeypatch.setattr("excel_agent.desktop.input.click", lambda *_a, **_k: None)
    save = ResolvedTarget(
        key="Save client",
        rect=Rect(4, 4, 8, 8),
        candidate_id="b1",
        source="uia-name",
    )
    monkeypatch.setattr(
        "excel_agent.agent.calibration.resolve_page_target",
        lambda *_a, **_k: (save, None),
    )
    monkeypatch.setattr(
        "excel_agent.agent.calibration.compare_saved_fields",
        lambda *_a, **_k: SimpleNamespace(detail="unchanged"),
    )
    monkeypatch.setattr(
        "excel_agent.agent.workspace.open_workspace",
        lambda _config: SimpleNamespace(path=tmp_path / "workbook.xlsx"),
    )
    writer = _RecordingWriter()
    monkeypatch.setattr(
        "excel_agent.excel.writer.WorkbookWriter.from_config",
        classmethod(lambda _cls, _config, path=None: writer),
    )
    def write_through(write, *args, **_kwargs):
        write(*args)
        return True, ""

    monkeypatch.setattr(
        "excel_agent.agent.pending_status.write_status_with_retries",
        write_through,
    )

    outcome = _save_live_row(
        config=_first_row_config(scrolling=False),
        row_number=2,
        values={"Client ID": "CL-1001"},
        fields={"Client ID": save},
        window=empty.window,
        session=tmp_path,
    )

    assert outcome.status == "RECHECK"
    assert writer.calls[0][0] == 2
    assert writer.calls[0][1] == "RECHECK"
    assert "none of the saved values appeared" in writer.calls[0][2]
