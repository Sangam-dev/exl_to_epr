"""Unit tests for the floating chat front end (python main.py --agent-ui)."""

from __future__ import annotations

import io
import sys
import threading

import pytest

from excel_agent.agent import chat_ui


def test_split_prompt_separates_question_and_hint() -> None:
    assert chat_ui._split_prompt("Run one row now? [Y/n]: ") == (
        "Run one row now?",
        "Y/n",
    )
    assert chat_ui._split_prompt("Enter Save button text in your form [Save]: ") == (
        "Enter Save button text in your form",
        "Save",
    )
    assert chat_ui._split_prompt("Press Enter to start visual grounding: ") == (
        "Press Enter to start visual grounding",
        "",
    )


def test_split_prompt_keeps_questions_that_only_mention_brackets() -> None:
    raw = (
        "Next step - [c]lick a button, [f]ill a field, "
        "[s]et the Save button, [d]one: "
    )
    label, hint = chat_ui._split_prompt(raw)
    assert hint == ""
    assert "[c]lick" in label


@pytest.mark.parametrize(
    ("line", "tag"),
    [
        ("[ OK ] Using sheet: 'Sheet1'", "ok"),
        ("  save confirmation: confirmed (success text matched '(?i)saved')", "ok"),
        ("Row 2: DONE", "ok"),
        ("[WARN] Could not read the window's controls: nope", "warn"),
        ("Row 2: RECHECK", "warn"),
        ("Stopped: Save declined by the user", "warn"),
        ("Traceback (most recent call last):", "err"),
        ("Error: Sheet has no header row.", "err"),
        ("Workbook: C:/data/out.xlsx", "text"),
        ("------------------------------------------------------------------------", "dim"),
        ("", "dim"),
    ],
)
def test_tag_for_colours_the_transcript(line: str, tag: str) -> None:
    assert chat_ui._tag_for(line) == tag


def test_gui_writer_buffers_until_a_newline_and_echoes() -> None:
    bridge = chat_ui.Bridge()
    echo = io.StringIO()
    writer = chat_ui._GuiWriter(bridge, echo=echo)

    writer.write("first")
    writer.write(" line\nsecond")

    assert echo.getvalue() == "first line\nsecond"
    assert bridge.lines.get_nowait() == "first line"
    assert bridge.lines.empty()

    writer.flush()
    assert bridge.lines.get_nowait() == "second"


def test_print_through_the_gui_writer_becomes_transcript_lines() -> None:
    bridge = chat_ui.Bridge()
    original = sys.stdout
    try:
        sys.stdout = chat_ui._GuiWriter(bridge)
        print("[ OK ] ready")
        print("two", "words")
    finally:
        sys.stdout = original

    assert bridge.lines.get_nowait() == "[ OK ] ready"
    assert bridge.lines.get_nowait() == "two words"


def test_bridge_ask_waits_for_the_window_answer() -> None:
    bridge = chat_ui.Bridge()
    answers: list[str] = []

    thread = threading.Thread(target=lambda: answers.append(bridge.ask("Window selection: ")))
    thread.start()

    ask = bridge.asks.get(timeout=2)
    assert ask.prompt == "Window selection: "
    assert thread.is_alive()

    ask.resolve("1")
    thread.join(timeout=2)
    assert answers == ["1"]


def test_bridge_ask_raises_eof_when_the_window_is_closed() -> None:
    bridge = chat_ui.Bridge()
    result: list[str] = []

    def worker() -> None:
        try:
            bridge.ask("Continue? ")
        except EOFError:
            result.append("eof")

    thread = threading.Thread(target=worker)
    thread.start()
    bridge.asks.get(timeout=2).cancel()
    thread.join(timeout=2)
    assert result == ["eof"]


def test_wizard_worker_routes_input_and_output_to_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = chat_ui.Bridge()
    seen: dict[str, object] = {}

    def fake_run_wizard(config_path=None, env_path=None):
        print("[ OK ] hello")
        seen["answer"] = input("Window selection: ")
        from excel_agent import cli

        seen["form_call"] = cli.run_form("cfg.json", confirm_each=True, limit=1)
        return 3

    monkeypatch.setattr("excel_agent.agent.wizard.run_wizard", fake_run_wizard)
    monkeypatch.setattr(
        "excel_agent.cli.run_form",
        lambda *args, **kwargs: seen.setdefault("kwargs", kwargs) and 0,
    )

    thread = threading.Thread(target=chat_ui._wizard_worker, args=(bridge, None, None))
    thread.start()

    assert bridge.lines.get(timeout=2) == "[ OK ] hello"
    bridge.asks.get(timeout=2).resolve("42")

    assert bridge.finished.wait(2)
    thread.join(timeout=2)

    assert bridge.exit_code == 3
    assert seen["answer"] == "42"
    assert seen["kwargs"] == {"confirm_each": False, "limit": 1}


def test_run_agent_chat_falls_back_when_tkinter_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_ui, "tk", None)
    calls: dict[str, object] = {}

    def fake_wizard(config_path=None, env_path=None):
        calls["args"] = (config_path, env_path)
        return 7

    monkeypatch.setattr("excel_agent.agent.wizard.run_wizard", fake_wizard)

    assert chat_ui.run_agent_chat(config_path="c.json", env_path=".env") == 7
    assert calls["args"] == ("c.json", ".env")
