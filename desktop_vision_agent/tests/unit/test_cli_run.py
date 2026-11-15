"""CLI wiring tests for the run confirmation flag (Segment 10)."""

from __future__ import annotations

from excel_agent.cli import build_parser


def test_run_accepts_confirm_each() -> None:
    args = build_parser().parse_args(["run", "--confirm-each"])

    assert args.command == "run"
    assert args.confirm_each is True


def test_run_rejects_an_unknown_flag() -> None:
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--confirm-each-everything"])
