# Segment 3 - Input layer & safety lock

**Version:** `v0.4-safety`
**Status:** implemented
**Date:** 2026-11-07

## Goal

Send mouse and keyboard input to the target application while blocking outside
input, with shortcuts that always release the lock.

## Deliverables

- `excel_agent/desktop/input.py`
  - Unicode keyboard input through `SendInput`,
  - single keys and modifier chords,
  - left/right mouse clicks,
  - clipboard paste through `pyperclip`,
  - every event tagged with the private `AGENT_TAG` value in `dwExtraInfo`.
- `excel_agent/safety/hotkeys.py`
  - `alt+<letter-or-digit>` parsing,
  - `Alt+T` stop, `Alt+P` pause, `Alt+R` resume,
  - duplicate-hotkey rejection.
- `excel_agent/safety/input_lock.py`
  - global `WH_KEYBOARD_LL` and `WH_MOUSE_LL` hooks on a dedicated thread,
  - agent-tagged events pass through,
  - external mouse and keyboard events are swallowed while blocking,
  - the three shortcuts are detected inside the hook,
  - passthrough mode keeps the keyboard hook alive so `Alt+R` works while
    paused.
- `excel_agent/safety/controller.py`
  - `IDLE -> RUNNING -> PAUSED -> STOPPED` state machine,
  - hotkey actions are queued and applied on a worker thread, never inside the
    hook callback,
  - `check()` returns `False` after stop and blocks while paused,
  - `wait(seconds)` stays responsive to stop/pause.
- `excel_agent/safety/watchdog.py`
  - stops the controller when the automation loop stops heartbeating,
  - the OS removes the hooks automatically if the process dies.
- `excel_agent/safety/overlay.py`
  - always-on-top status window showing running/paused/stopped and the active
    shortcuts,
  - `NullOverlay` for headless or test runs.
- `python main.py safety test` with `--seconds` and `--no-overlay`.
- Unit tests for hotkeys, keyboard decisions, tagged input, controller
  transitions, pause/resume wakeups, and the watchdog.

## Demo

```powershell
cd desktop_vision_agent
..\.venv\Scripts\python.exe main.py safety test --seconds 10
```

Observed:

```text
Safety test: external mouse and keyboard input will be blocked.
  Alt+T stop | Alt+P pause | Alt+R resume
  Ctrl+Alt+Del is handled by Windows and is never blocked.
  Locking for up to 10s; press Alt+T to release.

Input released (hotkey: stop).
```

The overlay appeared at the top-right of the screen and showed
`AGENT RUNNING | Alt+T stop | Alt+P pause`.

### Automated checks

With the WinForms fixture focused on its invoice text box:

| Check | Result |
|---|---|
| External `X` while locked | blocked (text box stayed empty) |
| Agent-tagged `Y` while locked | arrived |
| External `Alt+T` | stopped the controller, input released |
| External `Alt+P` | paused; external `z` then arrived |
| External `Alt+R` | resumed; external `X` blocked again |
| Agent-tagged `Y` after resume | arrived |
| External mouse move | blocked (cursor did not move) |
| Agent-tagged mouse move | arrived |
| Kill the locking process | hooks released; external mouse moved again |

## Acceptance

- External keyboard and mouse input is blocked while running.
- Agent-tagged input still works.
- `Alt+T` always releases the lock.
- `Ctrl+Alt+Del` is never intercepted; it remains the OS escape.
- A process crash or kill cannot leave input permanently locked.
- `pytest`: 95 passed.
- `python main.py --check`: passed.

## Decisions

- Hooks run on a dedicated thread with its own message loop. Hook callbacks do
  the minimum work: decide, enqueue, return.
- Agent input is identified by `dwExtraInfo`, not by timing or the injected
  flag. Other automation tools are therefore blocked as external input.
- Pause switches to passthrough instead of removing the hooks. This releases
  input but keeps `Alt+R` detectable.
- Key-ups belonging to a detected shortcut are allowed through. Without this, a
  blocked `Alt` key-up left the modifier stuck after `Alt+R`, and the next
  agent-typed character could be interpreted as a menu shortcut. This was found
  during the pause/resume integration check and fixed.
- The watchdog releases the lock after 120 seconds without a heartbeat. A single
  Gemini call can take up to 60 seconds, so later segments must beat between
  calls.
- The overlay is optional and runs Tk in its own daemon thread. It is small and
  topmost; later segments should hide it before taking screenshots if it
  overlaps the target window.
- `Ctrl+Alt+Del` is a secure attention sequence handled by Windows; low-level
  hooks cannot block it, which is exactly the intended escape hatch.

## Known limitations

- Only `Alt` + one ASCII letter or digit is supported for shortcuts.
- The lock is global; it is not limited to one application.
- Clipboard paste leaves the pasted text on the clipboard.
- Unicode typing requires the intended control to have keyboard focus.
- The overlay is topmost and can cover part of the target window.
- A hung process that is still alive is released by the watchdog; a killed
  process is released by Windows when its hook thread disappears.
