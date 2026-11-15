# Excel Desktop Agent

AI-first Windows desktop agent that reads rows from an Excel workbook and enters
them into a manually opened target application. Gemini grounds the form layout
once; deterministic Python fills the fields, verifies the save, and writes
`DONE` / `RECHECK` back to a copy of the workbook.

> **Status: Segment 10 (hardening, v0.9.10 save confirmation).** All 10
> segments (0 through 9) plus the save-confirmation hardening pass: unit and
> integration testing (334 tests), test fixture forms (WinForms and Tkinter),
> offline zero-cost demo runner, and fault recovery that never re-saves a row
> after Save was clicked.

## Requirements

- Windows 11
- Python 3.11+ (tested with 3.12.2)
- A Gemini API key

## Setup

```powershell
# from the workspace root
.\.venv\Scripts\python.exe -m pip install -r desktop_vision_agent\requirements.txt

cd desktop_vision_agent
Copy-Item .env.example .env
# edit .env and set GEMINI_API_KEY
```

## Environment check

```powershell
cd desktop_vision_agent
..\.venv\Scripts\python.exe main.py --check
```

The check verifies Python, dependencies, `.env`, the config file, the Gemini
model name, writable data folders, and the git repository. It never prints the
API key.

## Commands

| Command | Status |
|---|---|
| `python main.py --agent` | Interactive AI Assistant Wizard |
| `python main.py --agent-ui` | The same wizard in a floating chat window |
| `python main.py --check` | Implemented (Segment 0) |
| `python main.py --version` | Implemented (Segment 0) |
| `python main.py excel inspect` | Implemented (Segment 1) |
| `python main.py inspect` | Implemented (Segment 2) |
| `python main.py safety test` | Implemented (Segment 3) |
| `python main.py ground` | Implemented (Segment 4) |
| `python main.py setup` | Implemented (Segment 5) |
| `python main.py calibrate` | Implemented (Segment 6) |
| `python main.py run` | Implemented (Segment 7) |
| `python main.py form run` | Implemented (static step plan, no mapping) |
| `python main.py resume` | Implemented (Segment 8) |

## Interactive Agent Wizard (`--agent`)

The easiest way to run the agent on your own custom application and Excel file:

```powershell
..\.venv\Scripts\python.exe main.py --agent
```

The interactive wizard:
1. Lists open windows and lets you pick or search for your application.
2. Asks for your Excel file path, verifies sheets, and detects column headers.
3. Asks **single step** (AI mapping + calibration) or **multi step** (you
   describe each step in your own words).
4. Single step: generates a tailored configuration, grounds the layout with
   Gemini, gets your approval, then calibrates on the first row.
5. Multi step: resolves each step you describe, shows you what it understood,
   does it live on the first row, then saves that row when you start automation.
6. Prompts *"Do you want to start automation?"* and begins batch processing on your go.

## Floating Chat Window (`--agent-ui`)

The terminal wizard needs the terminal in front of you - and it covers the app
the agent has to look at. This runs the exact same wizard in a small window
that floats over the corner of your screen:

```powershell
..\.venv\Scripts\python.exe main.py --agent-ui
```

- Drag it by the title bar; `▾` collapses it to a strip, `◑` cycles opacity,
  `✕` closes it.
- It never takes over your app's window: answers go in the text box at the
  bottom (Enter or **Send**), and a blank answer means "accept the default".
- Every line the terminal wizard would print appears in the window, and the
  terminal keeps its own copy of the log.
- While automation is running, `✕` waits for it to finish; stop it with `Alt+T`.
- Chat mode never pauses before each Save, because confirming in the window
  would move focus off your app right before the click. Use
  `python main.py form run --confirm-each` in a terminal when you want to
  approve each save by hand.

If `tkinter` is unavailable, `--agent-ui` falls back to the terminal wizard.

## Safety

- `Alt+T` emergency stop, `Alt+P` pause, `Alt+R` resume.
- External input is blocked only while the agent is actively running.
- `Ctrl+Alt+Del` is never blocked.
- The original workbook is never modified.

Test the lock without running the agent:

```powershell
..\.venv\Scripts\python.exe main.py safety test --seconds 10
```

Input is blocked for 10 seconds, or until `Alt+T` is pressed.

## Grounding

`ground` sends one screenshot, the UIA control list, and the Excel headers to
Gemini, then validates the returned field map:

```powershell
..\.venv\Scripts\python.exe main.py ground --config config/agent.test.json
```

It writes `data/grounding/grounding_<stamp>.json` and an annotated screenshot
(red = fields, blue = buttons). Every API call is logged to `data/ai_logs`
with credentials redacted. The model is called once per grounding run, not per
field or per row.

## Mapping approval

`setup` grounds the window, validates the proposed column -> field mapping, and
asks you to approve it once:

```powershell
..\.venv\Scripts\python.exe main.py setup --config config/agent.test.json
```

The interactive editor accepts:

```text
set <column> <field_id>   assign a column to a grounded field
remove <column>           remove a mapping
show                      print the current mapping
done                      finish editing
cancel                    abort setup
```

Approval is refused while there are validation errors (unknown or duplicate
columns/fields, type mismatches, non-numeric values in a number column, missing
required columns). Low-confidence entries are warnings and need an explicit
`approve`. `--yes` approves non-interactively and accepts warnings.

The approved mapping is saved to `data/state/mapping.json` (override with
`--mapping`). Later segments reuse that file instead of asking Gemini again.

## First-row calibration

`calibrate` processes the first pending row and proves the mapping before
trusting it:

```powershell
..\.venv\Scripts\python.exe main.py calibrate --config config/agent.test.json
```

It re-inspects the live window, fills the mapped fields, reads them back
through UIA, takes a pre-save screenshot, and asks Gemini to confirm the
values. Only then does it click Save. After Save it reads the configured
success/error text region and marks the row `DONE` only on explicit
`CONFIRMED` evidence. Every attempt saves its screenshots to
`data/screenshots/`.

The mapping stays `trusted: false` until both checks pass. On failure the row
is marked `RECHECK` and the map stays untrusted. A save that may already have
succeeded is never retried automatically, so a failed calibration cannot
create duplicate records. `--no-overlay` disables the status overlay.

## Save confirmation

`confirmation` in the config decides what counts as a successful Save:

```json
"confirmation": {
  "success_region": { "x1": 0, "y1": 850, "x2": 1000, "y2": 950 },
  "error_region": { "x1": 0, "y1": 850, "x2": 1000, "y2": 950 },
  "success_pattern": "(?i)saved",
  "error_patterns": ["(?i)required", "(?i)duplicate", "(?i)error"]
}
```

Regions use 0-1000 normalized window coordinates. The agent crops the region
from the post-Save screenshot and reads its text through the injectable
`text_reader`; the only real reader in this segment is Gemini-backed. An OCR
reader can be added later without changing the strategy.

The outcome is explicit:

- `CONFIRMED` - the success pattern matched: the row becomes `DONE`.
- `REJECTED` - an error pattern matched: the row becomes `RECHECK` and is not
  saved again.
- `UNKNOWN` - no usable evidence: the row becomes `RECHECK` with
  `record may already exist`, never `DONE`.

Pixel comparison of the pre- and post-Save screenshots is only a **weak hint**
in the log. It can never confirm a save. If no confirmation pattern is
configured, every saved row is `UNKNOWN`, which fails closed to `RECHECK`.

`run --confirm-each` pauses for Enter or `Y` before each Save click, so you can
watch the first runs. Anything else stops the run before the click; the row is
left unsaved.

## Batch run

`run` processes every pending row with the trusted map:

```powershell
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json --limit 5
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json --confirm-each
```

`run` refuses to start while the mapping is untrusted, so calibration must pass
first. There is no override flag.

Each row is filled with the same deterministic path as calibration. Save is
judged only by the configured confirmation text, never by pixels and never by
a guess. A Save clicked once is never clicked again for that row in the same
run. Retries only cover failures before the click (fill, UIA read-back, or a
stop), so an uncertain Save cannot create a duplicate record.

By default `safety.stop_on_failure` is `true`: a non-`DONE` row stops the run
before the next row. Set it to `false` only when you deliberately want to
continue past `RECHECK` rows.

Rows are written and the workbook is saved after each row, so stopping mid-run
loses nothing. Restarting `run` skips every `DONE` and `RECHECK` row by default
(`excel.skip_statuses`).

The working copy is selected once and recorded in `data/state/output.json`.
`calibrate` and `run` reuse that same copy, which is how a calibrated row is
skipped by the batch runner. Delete that file to start a fresh copy from the
input workbook. If the recorded copy itself is missing, the agent refuses to
start rather than silently copying the input again, because that would lose
every status and risk duplicate records. `--no-overlay` disables the status
overlay.

## Static form mode (`form run`)

`run` needs a grounded, calibrated mapping, and a mapping can express *which*
field a column goes into but not the *order* of work. `form run` covers the
smaller case: a form whose controls you can name in the config, where the only
thing that matters is the sequence — click "Add" to open the entry fields, type
three text boxes, click Save.

```powershell
..\.venv\Scripts\python.exe main.py form run --config config/agent.steps.example.json --limit 5
```

To build this config without editing JSON, run `python main.py --agent` and
choose **2. Multi step**: describe each step in your words and the wizard turns
it into the plan below (see "Multi-step mode").

Add a `form` block to the config (see `config/agent.steps.example.json`):

```json
"form": {
  "steps": [
    { "action": "click", "button": "Add New" },
    { "action": "fill", "column": "Invoice Number" },
    { "action": "fill", "column": "Amount" },
    { "action": "click", "button": "Save Record" }
  ]
}
```

There are only two actions: `click` a button and `fill` a text field. A `fill`
uses the label from `fields` for its column; add `"field": "Other Label"` to a
step when the same column has to go into a differently named control. Every
fill step's column must be declared in `fields`, and `optional: true` on a step
turns a missing control into a note instead of a failure.

How it behaves:

- Targets are matched by UIA name in the live window, so the labels in the
  config must match what the application reports.
- The window is re-read after every click, so controls that the click created
  are found by the steps that follow it.
- Every fill is read back before Save. A mismatch is a `RECHECK`, not a retry,
  unless `form.retry_on_failure` is set.
- Save is clicked once and judged either by the configured confirmation text
  (`confirmation.verifier: "text"`) or, for apps that show no message, by the
  saved values appearing on screen (`"row"`: local, no AI call). A row that
  cannot be confirmed is `RECHECK`, never re-saved.
- No mapping, no calibration, no run state: rows marked `DONE` in the workbook
  are skipped, and that is the only progress record.

Not supported in this mode yet: dropdowns, date pickers, grids, and resume
after a stop. Use the mapped pipeline for forms that need those.

## Multi-step mode (`--agent`, choice 2)

The plan above has to be written as `click`/`fill` steps. Multi-step mode lets
you describe the work instead, and proves every step on the first row before
any batch run:

1. **Add a step.** Type an instruction in your own words ("click Add New",
   "fill up all the form"). The agent screenshots the window and asks Gemini
   once to turn that sentence into clicks and fills.
2. **Approve it.** It shows what it understood - which button, or which column
   goes into which field - and you answer with Enter (do it now), `r` (look at
   the screen again), or a correction in your own words ("the amount goes in
   Total, not Country").
3. **Watch it run.** Approved steps are performed at once, in order, on the
   first pending row. Every fill is read back, so a box that did not take the
   text is reported before the next step.
4. **Start automation.** The wizard writes `config/agent.interactive.steps.json`,
   clicks Save for the row you just filled, and records its status. It then asks
   whether the later saves should be checked at all - `[y]` checks each one,
   `[n]` fills and saves every row without looking at the screen - and, when
   checking, whether Gemini should read the screen after every Save or the new
   row should be found locally with no AI call. Finally it asks how many rows to
   process. Later rows replay the stored plan without asking anything.

The value that proves a save is decided from the row you just saved: whichever
column's value appeared on screen after it. If the screen shows nothing new, the
model is asked once, and the ID column is the last resort. With
`"verifier": "row"` the check stays local - the window is re-read after the
click and the row counts as saved when a value from `confirmation.footprint`
shows up more often than before and no form field still holds it. Text matched
by `confirmation.error_patterns` still rejects a save. Nothing is sent anywhere.

When the new row lands below the fold the local check scrolls down to look for
it, one screen at a time, and never scrolls back up - the Add button and the
form are expected to stay where they are. If the Add button itself scrolls away
the page scrolls as a whole, which this does not support yet: the run stops on
the row it was checking instead of clicking a button that is no longer there.
Tune it with `"scrolling": {"enabled": true, "max_screens": 6,
"settle_seconds": 0.4, "wheel_notches": 5}`; `"enabled": false` reads the
screen once, as before.

To skip the check altogether - fill and save every row and mark it `DONE`
without reading the screen - set `confirmation.verifier` to `"none"`, or answer
`[n]` to "Check the screen after each Save?" in the wizard. Nothing is read, no
row is scrolled for, and no `RECHECK` is written; a Save that the app refuses
will not be noticed. The first row is still checked, because that is how the
wizard tells the plan works.

Setup screenshots are kept in `data/steps/<timestamp>/`. Partial fills are
normal: a step fills only the columns it matched, and the last step that fills a
column wins. A step is never left unresolved - it is done, redone, or fixed by
your correction. Save is not a step you write; it is the final action.
Dropdowns, selects, and date pickers are still out of scope.

## Stop and resume

`run` records its progress in `data/state/run_state.json` when it starts and
after every row:

```powershell
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json
```

Press `Alt+T` mid-run. The row currently being filled is left without a
status; a row that was already saved carries `DONE` in the workbook. When you
stop:

- the row under way is recorded as unfinished but has no workbook status, so
  resuming restarts it from scratch instead of saving it twice,
- every finished row is recorded and never touched again.

Immediately before each Save click the agent writes a **write-ahead intent**
(`{row_number, invoice_id, state: "attempting_save", ts}`) to the run state and
flushes it to disk. If the process dies around Save, `resume` finds that
marker, marks the row `RECHECK` with
`interrupted around Save; verify in target app`, and does not retry it
automatically. A fresh `run` refuses to touch the working copy while such a
marker is unresolved; it asks you to `resume` first.

If the workbook cannot be written after Save (for example, it is open in
Excel), the outcome is retried with backoff. If it still fails, the run stops
with a clear message and the outcome is stored in both `run_state.json` and
`data/state/pending_status.json`. The next `run` or `resume` replays those
pending statuses before reading any row; it will not process a row while a
saved outcome is still unwritten.

Continue later:

```powershell
..\.venv\Scripts\python.exe main.py resume --config config/agent.test.json
```

`resume` refuses to start unless the mapping, config, input workbook, and
working copy all match the recorded run. It reuses the exact working copy
recorded in the run state (repairing `output.json` if needed), re-screenshots,
re-inspects UIA, and re-resolves the trusted map before touching the next row.
If the form layout changed structurally, it stops and asks you to run `setup`
again; it never re-grounds unattended.

A recorded run that can never be resumed — because the config, mapping, or
input workbook changed, or because the mapping was re-approved — is moved aside
as `run_state.stale-<timestamp>.json` and `run` starts a fresh batch on the
copy that run was using. A run whose working copy is missing blocks instead:
restore the copy, or delete the run state (and `output.json`) to start over,
accepting that rows already saved in the form may be processed again.

`data/state/run_state.json` is gitignored along with the other runtime state.

## Offline Demonstration

Run the complete pipeline (grounding, mapping, calibration, batch run, and resume)
with zero Gemini API token usage using the included mock demonstration:

```powershell
python scripts/demo_offline.py
```

## Testing

Run unit and live integration tests:

```powershell
pytest
```

To run with verbose output:

```powershell
pytest -v
```

## Troubleshooting

### 1. High-DPI Scaling & Coordinates
- Windows display scaling (125%, 150%, etc.) can cause coordinate misalignment if the target application is DPI-unaware.
- The agent detects DPI virtualization black padding and uses PerMonitorV2 awareness.
- When creating custom fixtures, ensure high-DPI mode is enabled.

### 2. Window Not Found or Minimized
- If `find_window` fails: verify that `app.title_contains` matches part of the visible window title.
- Minimized windows cannot be captured or interacted with; restore the target window before running `ground`, `calibrate`, or `run`.

### 3. Emergency Input Release
- If the agent blocks your mouse/keyboard and you need immediate control: press **`Alt+T`**.
- To temporarily pause execution: press **`Alt+P`** (and **`Alt+R`** to resume).
  The console prints `paused - press Alt+R to continue` and `resumed - continuing`.
  A pause takes effect between actions, never in the middle of typing a field;
  on resume the target window is brought back to the front (restored if it was
  minimised) and the screen is read again before the next click or fill, so the
  run does not type into whatever you left open. If you closed the form while it
  was paused, the row is written `RECHECK` instead of the Save button being
  clicked at a remembered point on the screen.
- Every click is verified against the live window immediately before it is
  sent: the point must still be inside the target window and land on the app
  itself (not on another application that has come in front). When it does not,
  nothing is clicked and the row stops with *"nothing was clicked: … is not over
  … any more"* — a stale coordinate can never click another window.
- A field whose first read-back disagrees is read once more after 150 ms before
  the row is failed, because a browser applies a paste a moment after the
  keystroke.
- `Ctrl+Alt+Del` is preserved at the Windows OS level as an unblockable escape.
- The background watchdog automatically releases input hooks if the process terminates or hangs.

## Implemented vs. Deferred (v0.9.10)

| Feature | Status | Notes |
|---|:---:|---|
| Text & Number inputs | Implemented | Filled via UIA and SendInput with clipboard fallback |
| Single Window Forms | Implemented | Native Windows applications |
| Visual Grounding | Implemented | Google Gemini (`gemini-3.6-flash`) |
| First-Row Calibration | Implemented | Pre-save Gemini check and post-save text confirmation |
| Deterministic Batch Entry | Implemented | One Gemini text-region read per saved row |
| Save Confirmation | Implemented | Config-driven success/error text; pixels are only a hint |
| Atomic Stop & Resume | Implemented | Write-ahead save intent plus `run_state.json` |
| Pending Status Replay | Implemented | `data/state/pending_status.json` sidecar |
| Low-Level Safety Lock | Implemented | Windows hooks with emergency `Alt+T` release |
| Combobox / Dropdowns | Deferred | Planned for v1.1 |
| Checkboxes / Radios | Deferred | Planned for v1.1 |
| Multi-tab / Wizards | Deferred | Planned for v1.1 |
| Automatic App Launch | Deferred | User opens target window manually |
| OCR text reader | Deferred | `TextReader` interface is ready; Gemini is the only reader |

## Layout

```text
desktop_vision_agent/
├── main.py
├── config/            # agent.example.json + prompt drafts
├── excel_agent/       # application package
├── tests/             # unit and integration test suites
├── scripts/           # test fixtures (test_form.ps1, test_form.py) and demo_offline.py
├── data/              # runtime input/output/screenshots/state (gitignored)
├── docs/segments/     # per-segment notes (segments 00-10)
└── legacy/            # archived experiments (not imported)
```

See `plan.md` for the full architecture and the 10-segment roadmap.
