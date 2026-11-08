# Excel Desktop Agent

AI-first Windows desktop agent that reads rows from an Excel workbook and enters
them into a manually opened target application. Gemini grounds the form layout
once; deterministic Python fills the fields, verifies the save, and writes
`DONE` / `RECHECK` back to a copy of the workbook.

> **Status: Segment 9 (Hardening & release, v1.0).** Full implementation of all 10
> segments (0 through 9): unit and integration testing (278 tests), test fixture
> forms (WinForms and Tkinter), offline zero-cost demo runner, and comprehensive
> fault recovery.

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
| `python main.py --check` | Implemented (Segment 0) |
| `python main.py --version` | Implemented (Segment 0) |
| `python main.py excel inspect` | Implemented (Segment 1) |
| `python main.py inspect` | Implemented (Segment 2) |
| `python main.py safety test` | Implemented (Segment 3) |
| `python main.py ground` | Implemented (Segment 4) |
| `python main.py setup` | Implemented (Segment 5) |
| `python main.py calibrate` | Implemented (Segment 6) |
| `python main.py run` | Implemented (Segment 7) |
| `python main.py resume` | Implemented (Segment 8) |

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
values. Only then does it click Save. A post-save screenshot is compared with
the pre-save image and sent to Gemini; the row is marked `DONE` only when both
checks pass. Every attempt saves its screenshots to `data/screenshots/`.

The mapping stays `trusted: false` until both checks pass. On failure the row
is marked `RECHECK` and the map stays untrusted. A save that may already have
succeeded is never retried automatically, so a failed calibration cannot
create duplicate records. `--no-overlay` disables the status overlay.

## Batch run

`run` processes every pending row with the trusted map:

```powershell
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json
..\.venv\Scripts\python.exe main.py run --config config/agent.test.json --limit 5
```

`run` refuses to start while the mapping is untrusted, so calibration must pass
first. There is no override flag.

Each row is filled with the same deterministic path as calibration. The save is
judged by comparing the non-empty mapped fields before and after Save: changed
fields mean `DONE`, unchanged fields are retried, and Gemini is called only
when the comparison cannot decide (no comparable field or a window-size
change). A clean run therefore makes **zero Gemini calls per row**.

A save that may already have succeeded is never retried; the row is marked
`RECHECK` instead. Rows are written and the workbook is saved after each row, so
stopping mid-run loses nothing. Restarting `run` skips every `DONE` row.

The working copy is selected once and recorded in `data/state/output.json`.
`calibrate` and `run` reuse that same copy, which is how a calibrated row is
skipped by the batch runner. Delete the file to start a fresh copy from the
input workbook. `--no-overlay` disables the status overlay.

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

Continue later:

```powershell
..\.venv\Scripts\python.exe main.py resume --config config/agent.test.json
```

`resume` refuses to start unless the mapping, config, input workbook, and
working copy all match the recorded run. It re-screenshots, re-inspects UIA,
and re-resolves the trusted map before touching the next row. If the form
layout changed structurally, it stops and asks you to run `setup` again; it
never re-grounds unattended. A stopped run can only be resumed — running `run`
again reports the recorded stop and tells you to use `resume` instead, or to
delete `data/state/run_state.json` to start a new run.

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
- `Ctrl+Alt+Del` is preserved at the Windows OS level as an unblockable escape.
- The background watchdog automatically releases input hooks if the process terminates or hangs.

## Implemented vs. Deferred (v1.0)

| Feature | Status | Notes |
|---|:---:|---|
| Text & Number inputs | Implemented | Filled via UIA and SendInput with clipboard fallback |
| Single Window Forms | Implemented | Native Windows applications |
| Visual Grounding | Implemented | Google Gemini (`gemini-3.6-flash`) |
| First-Row Calibration | Implemented | Pre-save and post-save visual verification |
| Deterministic Batch Entry | Implemented | 0 AI calls per clean row using pixel diffs |
| Atomic Stop & Resume | Implemented | Idempotent state persistence via `run_state.json` |
| Low-Level Safety Lock | Implemented | Windows hooks with emergency `Alt+T` release |
| Combobox / Dropdowns | Deferred | Planned for v1.1 |
| Checkboxes / Radios | Deferred | Planned for v1.1 |
| Multi-tab / Wizards | Deferred | Planned for v1.1 |
| Automatic App Launch | Deferred | User opens target window manually |

## Layout

```text
desktop_vision_agent/
├── main.py
├── config/            # agent.example.json + prompt drafts
├── excel_agent/       # application package
├── tests/             # unit and integration test suites
├── scripts/           # test fixtures (test_form.ps1, test_form.py) and demo_offline.py
├── data/              # runtime input/output/screenshots/state (gitignored)
├── docs/segments/     # per-segment notes (segments 00-09)
└── legacy/            # archived experiments (not imported)
```

See `plan.md` for the full architecture and the 10-segment roadmap.
