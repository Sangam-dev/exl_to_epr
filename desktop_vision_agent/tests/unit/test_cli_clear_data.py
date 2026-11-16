"""Tests for the ``clear-data`` command and its cleanup planner."""

from __future__ import annotations

from pathlib import Path

import pytest

from excel_agent.agent.cleanup import (
    CleanupPlan,
    PlannedFile,
    build_plan,
    execute_plan,
    is_inside,
)
from excel_agent.cli import build_parser, run_clear_data

DATA_DIRS = (
    "screenshots",
    "setup",
    "grounding",
    "steps",
    "annotated",
    "ai_logs",
    "logs",
    "output",
    "state",
    "input",
    "backup",
)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A miniature project tree with one file per category."""
    for name in DATA_DIRS:
        _write(tmp_path / "data" / name / ".gitkeep", "")
    _write(tmp_path / "data" / "screenshots" / "inspect_1.png")
    _write(tmp_path / "data" / "screenshots" / "notes.json")
    _write(tmp_path / "data" / "setup" / "setup_1.png")
    _write(tmp_path / "data" / "grounding" / "grounding_1.json")
    _write(tmp_path / "data" / "grounding" / "annotated_1.png")
    _write(tmp_path / "data" / "steps" / "20260101" / "step_1.png")
    _write(tmp_path / "data" / "steps" / "20260101" / "plan.json")
    _write(tmp_path / "data" / "annotated" / "old.png")
    _write(tmp_path / "data" / "ai_logs" / "call.json")
    _write(tmp_path / "data" / "logs" / "agent.log", "log line\n")
    _write(tmp_path / "data" / "logs" / "rerun.log", "rerun\n")
    _write(tmp_path / "data" / "output" / "invoices_working.xlsx")
    _write(tmp_path / "data" / "state" / "run_state.json", "{}")
    _write(tmp_path / "data" / "state" / "pending_status.json", "{}")
    _write(tmp_path / "data" / "state" / "output.json", "{}")
    _write(tmp_path / "data" / "state" / "mapping.json", "{}")
    _write(tmp_path / "data" / "state" / "run_state.stale-1.json", "{}")
    _write(tmp_path / "data" / "state" / "backup_manual" / "mapping.json", "{}")
    _write(tmp_path / "data" / "input" / "invoices.xlsx")
    _write(tmp_path / "data" / "backup" / "tree.tar.gz")
    _write(tmp_path / "config" / "agent.interactive.json", "{}")
    return tmp_path


def _pngs(root: Path) -> set[str]:
    return {path.name for path in root.rglob("*.png")}


def test_parser_accepts_categories_and_flags() -> None:
    args = build_parser().parse_args(
        ["clear-data", "--scrn", "--ai-logs", "--all", "--dry-run", "--yes", "--force"]
    )

    assert args.command == "clear-data"
    assert args.screenshots is True
    assert args.ai_logs is True
    assert args.all is True
    assert args.dry_run is True
    assert args.yes is True
    assert args.force is True


def test_parser_screenshots_long_alias() -> None:
    args = build_parser().parse_args(["clear-data", "--screenshots"])

    assert args.screenshots is True


def test_parser_rejects_unknown_flag() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["clear-data", "--everything-nice"])


def test_no_category_prints_usage(project: Path) -> None:
    assert run_clear_data([], root=project) == 2

    assert (project / "data" / "screenshots" / "inspect_1.png").is_file()


def test_dry_run_deletes_nothing(project: Path) -> None:
    assert run_clear_data(["all"], root=project, dry_run=True) == 0

    assert (project / "data" / "ai_logs" / "call.json").is_file()
    assert (project / "data" / "input" / "invoices.xlsx").is_file()
    assert build_plan(["all"], project).total_files > 0


def test_screenshots_keeps_plans_and_gitkeep(project: Path) -> None:
    assert run_clear_data(["screenshots"], root=project, assume_yes=True) == 0

    assert _pngs(project) == set()
    assert (project / "data" / "grounding" / "grounding_1.json").is_file()
    assert (project / "data" / "steps" / "20260101" / "plan.json").is_file()
    assert (project / "data" / "screenshots" / ".gitkeep").is_file()
    assert (project / "data" / "steps" / ".gitkeep").is_file()
    assert (project / "data" / "state" / "mapping.json").is_file()


def test_output_also_clears_workspace_pointer(project: Path) -> None:
    assert run_clear_data(["output"], root=project, assume_yes=True) == 0

    assert not (project / "data" / "output" / "invoices_working.xlsx").exists()
    assert not (project / "data" / "state" / "output.json").exists()
    # The input workbook is never the category being cleared.
    assert (project / "data" / "input" / "invoices.xlsx").is_file()


def test_state_keeps_mapping_and_spares_demo_files(project: Path) -> None:
    _write(project / "data" / "state" / "demo_mapping.json", "{}")
    _write(project / "data" / "state" / "run.lock", '{"pid": 99999999}')

    assert run_clear_data(["state"], root=project, assume_yes=True) == 0

    assert not (project / "data" / "state" / "run_state.json").exists()
    assert not (project / "data" / "state" / "run_state.stale-1.json").exists()
    assert not (project / "data" / "state" / "pending_status.json").exists()
    assert not (project / "data" / "state" / "output.json").exists()
    assert not (project / "data" / "state" / "run.lock").exists()
    assert not (project / "data" / "state" / "backup_manual").exists()
    assert (project / "data" / "state" / "mapping.json").is_file()
    assert (project / "data" / "state" / "demo_mapping.json").is_file()
    assert (project / "data" / "state" / ".gitkeep").is_file()


def test_state_refuses_queued_statuses_without_force(project: Path) -> None:
    _write(
        project / "data" / "state" / "pending_status.json",
        '{"version": 1, "entries": [{"row_number": 2, "status": "DONE"}]}',
    )

    assert run_clear_data(["state"], root=project, assume_yes=True) == 1

    assert (project / "data" / "state" / "pending_status.json").is_file()


def test_state_force_discards_queued_statuses(project: Path) -> None:
    _write(
        project / "data" / "state" / "pending_status.json",
        '{"version": 1, "entries": [{"row_number": 2, "status": "DONE"}]}',
    )

    assert run_clear_data(["state"], root=project, assume_yes=True, force=True) == 0

    assert not (project / "data" / "state" / "pending_status.json").exists()


def test_state_refuses_when_a_run_holds_the_lock(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / "data" / "state" / "run.lock", '{"pid": 4242}')
    monkeypatch.setattr("excel_agent.agent.run_lock._pid_alive", lambda pid: True)

    assert run_clear_data(["state"], root=project, assume_yes=True) == 1

    assert (project / "data" / "state" / "run.lock").is_file()


def test_all_spares_input_and_mapping(project: Path) -> None:
    assert run_clear_data(["all"], root=project, assume_yes=True) == 0

    assert (project / "data" / "input" / "invoices.xlsx").is_file()
    assert (project / "data" / "state" / "mapping.json").is_file()
    assert not (project / "data" / "ai_logs" / "call.json").exists()
    assert not (project / "data" / "backup" / "tree.tar.gz").exists()
    assert (project / "data" / "ai_logs" / ".gitkeep").is_file()


def test_mapping_and_input_are_opt_in(project: Path) -> None:
    assert run_clear_data(["input", "mapping"], root=project, assume_yes=True) == 0

    assert not (project / "data" / "input" / "invoices.xlsx").exists()
    assert not (project / "data" / "state" / "mapping.json").exists()


def test_prompt_declined_keeps_files(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert run_clear_data(["ai_logs"], root=project) == 0

    assert (project / "data" / "ai_logs" / "call.json").is_file()


def test_prompt_accepted_deletes_files(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert run_clear_data(["ai_logs"], root=project) == 0

    assert not (project / "data" / "ai_logs" / "call.json").exists()


def test_locked_log_is_emptied_not_skipped(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_unlink = Path.unlink

    def fake_unlink(self: Path, *args, **kwargs):
        if self.name == "agent.log":
            raise PermissionError("file is open in another program")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert run_clear_data(["logs"], root=project, assume_yes=True) == 0

    assert (project / "data" / "logs" / "agent.log").is_file()
    assert (project / "data" / "logs" / "agent.log").read_text(encoding="utf-8") == ""
    assert not (project / "data" / "logs" / "rerun.log").exists()


def test_plan_paths_stay_inside_the_project(project: Path, tmp_path: Path) -> None:
    outside = _write(tmp_path.parent / "outside_clear_data.txt")
    plan = build_plan(["ai_logs"], project)

    assert plan.total_files == 1
    assert is_inside(plan.files[0].path, project) is True
    assert is_inside(outside, project) is False


def test_plan_items_outside_root_are_skipped(project: Path, tmp_path: Path) -> None:
    outside = _write(tmp_path.parent / "outside_clear_data_2.txt")

    from excel_agent.agent.cleanup import CategoryPlan

    plan = CleanupPlan(
        categories=(
            CategoryPlan(
                key="ai_logs",
                label="AI logs",
                files=(PlannedFile(path=outside, size=1),),
            ),
        )
    )
    result = execute_plan(plan, project)

    assert [reason for _, reason in result.skipped] == ["outside the project folder"]
    assert outside.is_file()
    assert result.deleted == []


def test_category_selection_reads_cleanup_plan(project: Path) -> None:
    plan = build_plan(["screenshots", "logs"], project)

    assert [group.key for group in plan.categories] == ["screenshots", "logs"]
    assert plan.total_files == 7
