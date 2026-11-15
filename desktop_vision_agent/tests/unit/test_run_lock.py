"""The lock that keeps two runs from driving the same application."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from excel_agent.agent import run_lock
from excel_agent.agent.run_lock import RunLock, RunLockError


@pytest.fixture(autouse=True)
def _own_pid_is_alive(monkeypatch):
    """Pretend every recorded pid is alive unless a test says otherwise."""

    monkeypatch.setattr(run_lock, "_pid_alive", lambda pid: pid > 0)


def test_the_lock_can_be_taken_and_released(tmp_path: Path) -> None:
    lock = RunLock(tmp_path / "run.lock")

    lock.acquire()
    assert lock.path.is_file()
    assert json.loads(lock.path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    lock.release()
    assert not lock.path.exists()


def test_a_second_run_is_refused_while_the_first_holds_the_lock(
    tmp_path: Path,
) -> None:
    first = RunLock(tmp_path / "run.lock")
    second = RunLock(tmp_path / "run.lock")
    first.acquire()

    with pytest.raises(RunLockError) as excinfo:
        second.acquire()

    assert "another run is already active" in str(excinfo.value)
    first.release()


def test_a_lock_left_by_a_crashed_run_is_taken_over(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": 999_999}), encoding="utf-8")
    monkeypatch.setattr(run_lock, "_pid_alive", lambda _pid: False)

    lock = RunLock(path)
    lock.acquire()

    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()
    assert not path.exists()


def test_a_corrupt_lock_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text("not json", encoding="utf-8")

    lock = RunLock(path)
    lock.acquire()

    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()


def test_a_stray_release_leaves_another_run_in_place(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": 424_242}), encoding="utf-8")

    # A lock this object never took must not be removed by a stray release.
    RunLock(path).release()

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == 424_242


def test_the_lock_works_as_a_context_manager(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"

    with RunLock(path):
        assert path.exists()

    assert not path.exists()


def test_a_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    first = RunLock(path)
    first.acquire()
    first.release()

    second = RunLock(path)
    second.acquire()
    second.release()
    assert not path.exists()
