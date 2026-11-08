# Excel Desktop Agent

AI-first Windows desktop agent that reads rows from an Excel workbook and enters
them into a manually opened target application. Gemini grounds the form layout
once; deterministic Python fills the fields, verifies the save, and writes
`DONE` / `RECHECK` back to a copy of the workbook.

> **Status: Segment 5 (mapping & approval).** Window perception, the input
> layer, the global input lock, stop/pause/resume, one-shot Gemini grounding,
> and one-time mapping approval are implemented. Calibration and the row runner
> are stubs.

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
| `python main.py calibrate` | Segment 6 |
| `python main.py run` | Segment 7 |
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
