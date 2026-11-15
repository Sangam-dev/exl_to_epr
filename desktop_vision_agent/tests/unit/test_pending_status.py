"""Unit tests for the pending-status sidecar (Segment 10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from excel_agent.agent.pending_status import (
    PENDING_STATUS_VERSION,
    PendingStatus,
    PendingStatusError,
    PendingStatusFile,
    apply_pending_statuses,
    load_pending_status,
    queue_pending_status,
    save_pending_status,
    write_status_with_retries,
)


class FakeWriter:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[int, str, str]] = []
        self.attempts = 0

    def write_status(
        self, row_number: int, status: str, error: str = "", timestamp=None
    ) -> None:
        self.attempts += 1
        if self.failures > 0:
            self.failures -= 1
            raise OSError("workbook is open in Excel")
        self.calls.append((row_number, status, error))


def test_pending_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    entry = PendingStatus(
        row_number=4,
        status="DONE",
        error="",
        attempts=1,
        created_at="2026-11-13T10:00:00+00:00",
    )
    save_pending_status(PendingStatusFile(entries=(entry,)), path)

    loaded = load_pending_status(path)

    assert loaded is not None
    assert loaded.version == PENDING_STATUS_VERSION
    assert loaded.entries == (entry,)


def test_queue_replaces_an_existing_row_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "state" / "pending_status.json"

    queue_pending_status(
        row_number=4, status="RECHECK", error="first", path=path
    )
    queued = queue_pending_status(
        row_number=4, status="DONE", error="", path=path
    )

    assert len(queued.entries) == 1
    assert queued.entries[0].row_number == 4
    assert queued.entries[0].status == "DONE"
    loaded = load_pending_status(path)
    assert loaded is not None
    assert loaded.entries[0].status == "DONE"


def test_queue_keeps_multiple_rows(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"

    queue_pending_status(row_number=4, status="DONE", path=path)
    queue_pending_status(row_number=5, status="RECHECK", path=path)

    loaded = load_pending_status(path)
    assert loaded is not None
    assert [entry.row_number for entry in loaded.entries] == [4, 5]


def test_load_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(PendingStatusError, match="cannot parse"):
        load_pending_status(path)


def test_load_rejects_an_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    payload = {"version": 99, "entries": []}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PendingStatusError, match="version"):
        load_pending_status(path)


def test_load_rejects_a_bad_entry(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    payload = {"version": PENDING_STATUS_VERSION, "entries": [{"status": "DONE"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PendingStatusError, match="row_number"):
        load_pending_status(path)


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "state" / "pending_status.json"
    save_pending_status(
        PendingStatusFile(
            entries=(PendingStatus(row_number=4, status="DONE"),)
        ),
        path,
    )

    assert path.is_file()
    assert list(path.parent.glob(".pending_status_*")) == []


def test_write_status_retries_with_backoff() -> None:
    writer = FakeWriter(failures=2)
    delays: list[float] = []

    ok, failure = write_status_with_retries(
        writer.write_status,
        4,
        "DONE",
        error="",
        attempts=3,
        base_delay=0.5,
        sleep=delays.append,
    )

    assert ok is True
    assert failure == ""
    assert writer.calls == [(4, "DONE", "")]
    assert delays == [0.5, 1.0]


def test_write_status_reports_the_last_failure() -> None:
    writer = FakeWriter(failures=99)
    delays: list[float] = []

    ok, failure = write_status_with_retries(
        writer.write_status,
        4,
        "DONE",
        attempts=2,
        base_delay=0.25,
        sleep=delays.append,
    )

    assert ok is False
    assert "Excel" in failure
    assert delays == [0.25]


def test_apply_pending_statuses_writes_and_clears(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    queue_pending_status(row_number=4, status="DONE", path=path)
    queue_pending_status(row_number=5, status="RECHECK", error="verify", path=path)
    writer = FakeWriter()

    applied, remaining = apply_pending_statuses(
        writer.write_status, path=path, output=lambda _line: None
    )

    assert applied == (4, 5)
    assert remaining == ()
    assert writer.calls == [(4, "DONE", ""), (5, "RECHECK", "verify")]
    assert load_pending_status(path) is None


def test_apply_pending_statuses_keeps_failures(tmp_path: Path) -> None:
    path = tmp_path / "pending_status.json"
    queue_pending_status(row_number=4, status="DONE", path=path)
    writer = FakeWriter(failures=99)

    applied, remaining = apply_pending_statuses(
        writer.write_status,
        path=path,
        attempts=1,
        sleep=lambda _seconds: None,
        output=lambda _line: None,
    )

    assert applied == ()
    assert len(remaining) == 1
    assert remaining[0].row_number == 4
    assert remaining[0].attempts == 1
    assert "Excel" in remaining[0].error
    loaded = load_pending_status(path)
    assert loaded is not None
    assert loaded.entries[0].row_number == 4


def test_apply_without_a_sidecar_is_a_noop(tmp_path: Path) -> None:
    writer = FakeWriter()

    applied, remaining = apply_pending_statuses(
        writer.write_status,
        path=tmp_path / "missing.json",
        output=lambda _line: None,
    )

    assert applied == ()
    assert remaining == ()
    assert writer.calls == []
