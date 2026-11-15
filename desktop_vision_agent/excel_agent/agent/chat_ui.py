"""Floating chat window for the interactive wizard (`python main.py --agent-ui`).

The terminal wizard works, but it needs the terminal in front of the app it is
driving - and the terminal then covers the very window the agent has to see.
This module keeps the same conversation in a small always-on-top window parked
in the corner of the screen, so the target application stays uncovered.

It is a *front end*, not a second wizard: :func:`excel_agent.agent.wizard.run_wizard`
runs unmodified in a worker thread, ``input()`` is routed to the chat box, and
``print()`` is teed into the transcript (the terminal still gets a full copy).

Everything that decides anything lives in the wizard; this file only moves
bytes between the wizard and the widgets.
"""

from __future__ import annotations

import io
import queue
import re
import sys
import threading
import traceback

try:  # tkinter is optional; the terminal wizard still works without it
    import tkinter as tk
except Exception:  # pragma: no cover - depends on the Python build
    tk = None

from ..desktop.topmost import keep_above_topmost


WINDOW_WIDTH = 470
WINDOW_HEIGHT = 360
HEADER_HEIGHT = 32

BG = "#000000"
PANEL = "#1b1f26"
TRANSCRIPT_BG = "#14171c"
FIELD_BG = "#232a34"
TEXT = "#e7eaee"
DIM = "#8b949e"
ACCENT = "#5aa9e6"
OK = "#67d391"
WARN = "#e8c468"
ERR = "#f2777a"
BORDER = "#2f3540"

SOURCE_FONT = ("Segoe UI", 9)
MONO_FONT = ("Consolas", 8)


# ---------------------------------------------------------------------------
# Pure helpers (no tkinter, so they are testable without a display)
# ---------------------------------------------------------------------------


_PROMPT_HINT = re.compile(r"^(?P<label>.*?)\s*\[(?P<hint>[^\]]*)\]\s*[:?]?\s*$")


def _split_prompt(raw: str) -> tuple[str, str]:
    """Split ``"Run one row now? [Y/n]: "`` into its question and hint."""
    text = (raw or "").rstrip()
    if text.endswith(":"):
        text = text[:-1]
    match = _PROMPT_HINT.match(text)
    if match:
        label = match.group("label").strip()
        hint = match.group("hint").strip()
        return (label or text.strip(), hint)
    return text.strip(), ""


def _tag_for(line: str) -> str:
    """Pick the transcript colour tag for one output line."""
    text = line.strip()
    lowered = text.casefold()
    if not text:
        return "dim"
    if "traceback" in lowered or lowered.startswith("error") or "failed" in lowered:
        return "err"
    if (
        "recheck" in lowered
        or "[warn]" in lowered
        or lowered.startswith("stopping")
        or lowered.startswith("stopped")
    ):
        return "warn"
    if (
        text.startswith("[ OK ]")
        or "confirmed" in lowered
        or lowered.endswith(": done")
        or " done (" in lowered
    ):
        return "ok"
    if text.startswith("---") or text.startswith("==="):
        return "dim"
    if text.startswith("STEP "):
        return "step"
    if lowered.startswith("  step "):
        return "dim"
    return "text"


class Bridge:
    """Thread-safe hand-off between the wizard thread and the Tk main thread."""

    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()
        self.asks: queue.Queue[_Ask] = queue.Queue()
        self.finished = threading.Event()
        self.exit_code = 0

    def push(self, line: str) -> None:
        self.lines.put(line)

    def finish(self, code: int) -> None:
        self.exit_code = code
        self.finished.set()

    def ask(self, prompt: str = "") -> str:
        """Block the caller until the user answers in the chat window."""
        ask = _Ask(prompt)
        self.asks.put(ask)
        ask.done.wait()
        if ask.cancelled:
            raise EOFError("the chat window was closed")
        return ask.answer


class _Ask:
    """One pending ``input()`` call."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.answer = ""
        self.cancelled = False
        self.done = threading.Event()

    def resolve(self, answer: str) -> None:
        if self.done.is_set():
            return
        self.answer = answer
        self.done.set()

    def cancel(self) -> None:
        if self.done.is_set():
            return
        self.cancelled = True
        self.done.set()


class _GuiWriter(io.TextIOBase):
    """File-like object that turns ``print()`` into transcript lines."""

    def __init__(self, bridge: Bridge, echo: io.TextIOBase | None = None) -> None:
        super().__init__()
        self._bridge = bridge
        self._echo = echo
        self._buffer = ""

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        if not text:
            return 0
        if self._echo is not None:
            try:
                self._echo.write(text)
                self._echo.flush()
            except Exception:
                pass
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._bridge.push(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            line, self._buffer = self._buffer, ""
            self._bridge.push(line)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


class ChatWindow:
    """A small, draggable, semi-transparent window that hosts the wizard."""

    def __init__(self, config_path: str | None = None, env_path: str | None = None) -> None:
        if tk is None:
            raise RuntimeError("tkinter is unavailable")
        self.bridge = Bridge()
        self._config_path = config_path
        self._env_path = env_path
        self._worker: threading.Thread | None = None
        self._pending: _Ask | None = None
        self._collapsed = False
        self._close_requested = False
        self._finished_shown = False
        self._idle_hint_shown = False
        self._drag: tuple[int, int] | None = None
        self._alpha_index = 0
        self._alphas = (0.92, 0.78, 0.62)

        self.root = tk.Tk()
        # Windows text scales with the display, pixels do not: size the window
        # from the real DPI so the input row is never squeezed out.
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self._width = int(WINDOW_WIDTH * scale)
        self._height = int(WINDOW_HEIGHT * scale)
        self._collapsed_height = int(HEADER_HEIGHT * scale) + 4
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        root = self.root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        # The target app can be pinned on top too, so the flag alone is not
        # enough: keep winning the topmost band back, without taking focus.
        keep_above_topmost(root)
        root.attributes("-alpha", self._alphas[self._alpha_index])
        root.configure(bg=BORDER)
        screen = root.winfo_screenwidth()
        x = max(0, screen - self._width - 16)
        root.geometry(f"{self._width}x{self._height}+{x}+72")

        outer = tk.Frame(root, bg=BORDER)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(outer, bg=PANEL, cursor="fleur")
        header.pack(fill="x")
        header.bind("<ButtonPress-1>", self._start_drag)
        header.bind("<B1-Motion>", self._on_drag)

        title = tk.Label(
            header,
            text="Excel Agent",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=6,
            cursor="fleur",
        )
        title.pack(side="left")
        title.bind("<ButtonPress-1>", self._start_drag)
        title.bind("<B1-Motion>", self._on_drag)

        self._status = tk.Label(
            header,
            text="starting",
            bg=PANEL,
            fg=DIM,
            font=SOURCE_FONT,
        )
        self._status.pack(side="left", padx=(4, 0))

        for glyph, command in (
            ("\u2715", self._on_close),
            ("\u25be", self._toggle_collapse),
            ("\u25d1", self._cycle_alpha),
        ):
            button = tk.Label(
                header,
                text=glyph,
                bg=PANEL,
                fg=DIM,
                font=("Segoe UI", 9),
                padx=7,
                pady=6,
                cursor="hand2",
            )
            button.pack(side="right")
            button.bind("<Button-1>", lambda _event, fn=command: fn())
            button.bind("<Enter>", lambda event: event.widget.configure(fg=TEXT))
            button.bind("<Leave>", lambda event: event.widget.configure(fg=DIM))

        body = tk.Frame(outer, bg=TRANSCRIPT_BG)
        body.pack(fill="both", expand=True)
        self._body = body

        # Pack the input row and the question first, from the bottom up, so a
        # long transcript can never push them off the window.
        entry_row = tk.Frame(body, bg=TRANSCRIPT_BG)
        entry_row.pack(side="bottom", fill="x", padx=8, pady=(4, 8))

        self.entry = tk.Entry(
            entry_row,
            bg=FIELD_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=SOURCE_FONT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            state="disabled",
            disabledbackground=FIELD_BG,
            disabledforeground=DIM,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Button-1>", self._on_entry_click)

        self._send = tk.Label(
            entry_row,
            text="Send",
            bg="#2a5f8a",
            fg=TEXT,
            font=SOURCE_FONT,
            padx=10,
            pady=5,
            cursor="hand2",
        )
        self._send.pack(side="left", padx=(6, 0))
        self._send.bind("<Button-1>", lambda _event: self._submit())

        self._prompt_label = tk.Label(
            body,
            text="Starting the agent...",
            bg=TRANSCRIPT_BG,
            fg=ACCENT,
            font=SOURCE_FONT,
            anchor="w",
            justify="left",
            wraplength=self._width - 30,
        )
        self._prompt_label.pack(side="bottom", fill="x", padx=10, pady=(2, 0))

        self.text = tk.Text(
            body,
            bg=TRANSCRIPT_BG,
            fg=TEXT,
            relief="flat",
            wrap="char",
            font=SOURCE_FONT,
            padx=8,
            pady=6,
            height=6,
            highlightthickness=0,
            state="disabled",
            cursor="arrow",
        )
        self.text.pack(fill="both", expand=True, padx=2, pady=(4, 2))
        for tag, colour, font in (
            ("text", TEXT, SOURCE_FONT),
            ("dim", DIM, SOURCE_FONT),
            ("ok", OK, SOURCE_FONT),
            ("warn", WARN, SOURCE_FONT),
            ("err", ERR, SOURCE_FONT),
            ("step", TEXT, SOURCE_FONT),
            ("you", ACCENT, MONO_FONT),
        ):
            self.text.tag_configure(tag, foreground=colour, font=font)
        self.text.bind("<MouseWheel>", self._on_wheel)

    # -- window chrome -----------------------------------------------------

    def _start_drag(self, event) -> None:
        self._drag = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_drag(self, event) -> None:
        if self._drag is None:
            return
        offset_x, offset_y = self._drag
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _on_wheel(self, event) -> str:
        self.text.yview_scroll(-1 * (event.delta // 120), "units")
        return "break"

    def _cycle_alpha(self) -> None:
        self._alpha_index = (self._alpha_index + 1) % len(self._alphas)
        self.root.attributes("-alpha", self._alphas[self._alpha_index])

    def _toggle_collapse(self) -> None:
        x, y = self.root.winfo_x(), self.root.winfo_y()
        if self._collapsed:
            self._body.pack(fill="both", expand=True)
            self.root.geometry(f"{self._width}x{self._height}+{x}+{y}")
        else:
            self._body.pack_forget()
            self.root.geometry(f"{self._width}x{self._collapsed_height}+{x}+{y}")
        self._collapsed = not self._collapsed

    def _on_entry_click(self, _event) -> str | None:
        if self._pending is not None:
            return None
        if not self._idle_hint_shown:
            self._idle_hint_shown = True
            self._log("[The box unlocks as soon as the agent asks you something]", "dim")
        return "break"

    def _on_close(self) -> None:
        if self._pending is not None:
            ask, self._pending = self._pending, None
            ask.cancel()
            while True:
                try:
                    self.bridge.asks.get_nowait().cancel()
                except queue.Empty:
                    break
            self._close_requested = True
            self._log("[Cancelled from the chat window]", "warn")
            return
        if self._worker is not None and self._worker.is_alive():
            self._log(
                "[Still working - Alt+T stops the automation, or wait for it to finish]",
                "warn",
            )
            return
        self.root.destroy()

    # -- transcript --------------------------------------------------------

    def _log(self, line: str, tag: str | None = None) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag or _tag_for(line))
        self.text.configure(state="disabled")
        self.text.see("end")

    def _drain_lines(self) -> None:
        while True:
            try:
                line = self.bridge.lines.get_nowait()
            except queue.Empty:
                return
            self._log(line)

    # -- input -------------------------------------------------------------

    def _set_input_enabled(self, enabled: bool) -> None:
        self.entry.configure(state="normal" if enabled else "disabled")
        self._send.configure(bg="#2a5f8a" if enabled else PANEL, fg=TEXT if enabled else DIM)

    def _begin_ask(self, ask: _Ask) -> None:
        self._pending = ask
        label, hint = _split_prompt(ask.prompt)
        if hint:
            self._prompt_label.configure(text=f"{label}   [{hint}]")
        else:
            self._prompt_label.configure(text=label or "(Enter to continue)")
        self._status.configure(text="waiting for you", fg=ACCENT)
        self._set_input_enabled(True)
        try:
            self.root.lift()
            self.root.focus_force()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _submit(self, _event=None) -> str:
        if self._pending is None:
            return "break"
        answer = self.entry.get()
        self.entry.delete(0, "end")
        ask, self._pending = self._pending, None
        self._log(f"> {answer if answer else '(Enter)'}", "you")
        self._set_input_enabled(False)
        self._prompt_label.configure(text="Working...")
        self._status.configure(text="working", fg=DIM)
        ask.resolve(answer)
        return "break"

    # -- lifecycle ---------------------------------------------------------

    def _poll(self) -> None:
        self._drain_lines()

        if self._pending is None:
            try:
                ask = self.bridge.asks.get_nowait()
            except queue.Empty:
                ask = None
            if ask is not None:
                self._begin_ask(ask)

        if self.bridge.finished.is_set() and not self._finished_shown:
            self._finished_shown = True
            self._set_input_enabled(False)
            self._status.configure(text="finished", fg=DIM)
            self._prompt_label.configure(
                text=f"Session finished (exit code {self.bridge.exit_code}). Close when ready."
            )

        if self._close_requested and self._finished_shown:
            self.root.destroy()
            return

        self.root.after(80, self._poll)

    def run(self) -> int:
        self._worker = threading.Thread(
            target=_wizard_worker,
            args=(self.bridge, self._config_path, self._env_path),
            name="chat-wizard",
            daemon=True,
        )
        self._worker.start()
        self.root.after(80, self._poll)
        try:
            self.root.mainloop()
        finally:
            if self._pending is not None:
                self._pending.cancel()
        return self.bridge.exit_code


# ---------------------------------------------------------------------------
# Worker: the wizard, with input()/print() pointed at the window
# ---------------------------------------------------------------------------


def _wizard_worker(
    bridge: Bridge, config_path: str | None, env_path: str | None
) -> None:
    """Run the unchanged wizard with its console redirected to the chat window."""
    import builtins

    from .. import cli as cli_module
    from . import wizard

    stdout, stderr = sys.stdout, sys.stderr
    real_input = builtins.input
    real_run_form = cli_module.run_form

    def chat_input(prompt: str = "") -> str:
        return bridge.ask(prompt)

    def run_form_without_pause(*args, **kwargs):
        # A per-Save pause would hand focus to this window right before the
        # click, so the click would land on the chat instead of the app.
        if kwargs.get("confirm_each"):
            print(
                "[WARN] Chat mode skips the per-Save pause: confirming here "
                "would move focus off your app before the click. Use "
                "'python main.py form run --confirm-each' in a terminal when "
                "you want to approve every save."
            )
        kwargs["confirm_each"] = False
        return real_run_form(*args, **kwargs)

    code = 0
    builtins.input = chat_input
    cli_module.run_form = run_form_without_pause
    sys.stdout = _GuiWriter(bridge, echo=stdout)
    sys.stderr = _GuiWriter(bridge, echo=stderr)
    try:
        code = wizard.run_wizard(config_path=config_path, env_path=env_path)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        cli_module.run_form = real_run_form
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        sys.stdout, sys.stderr = stdout, stderr
        builtins.input = real_input
        bridge.finish(code)


def run_agent_chat(config_path: str | None = None, env_path: str | None = None) -> int:
    """Run the interactive wizard inside the floating chat window."""
    if tk is None:
        print("tkinter is unavailable, so the terminal wizard will be used instead.")
        from .wizard import run_wizard

        return run_wizard(config_path=config_path, env_path=env_path)
    window = ChatWindow(config_path=config_path, env_path=env_path)
    return window.run()
