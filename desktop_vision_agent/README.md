# Excel Desktop Agent

AI-first Windows desktop agent that reads rows from an Excel workbook and enters
them into a manually opened target application. Gemini grounds the form layout
once; deterministic Python fills the fields, verifies the save, and writes
`DONE` / `RECHECK` back to a copy of the workbook.

> **Status: Segment 7 (batch row runner).** Window perception, the input layer,
> the global input lock, stop/pause/resume, one-shot Gemini grounding, one-time
> mapping approval, first-row calibration, and batch row processing are
> implemented. Stop/resume state across process restarts is a stub.

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
| `python main.py resume` | Segment 8 |

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

## Layout

```text
desktop_vision_agent/
├── main.py
├── config/            # agent.example.json + prompt drafts
├── excel_agent/       # application package
├── tests/
├── data/              # runtime input/output/screenshots/state (gitignored)
├── docs/segments/     # per-segment notes
└── legacy/            # archived experiments (not imported)
```

See `plan.md` for the full architecture and the 10-segment roadmap.
