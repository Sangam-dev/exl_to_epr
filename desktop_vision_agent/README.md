# Excel Desktop Agent

AI-first Windows desktop agent that reads rows from an Excel workbook and enters
them into a manually opened target application. Gemini grounds the form layout
once; deterministic Python fills the fields, verifies the save, and writes
`DONE` / `RECHECK` back to a copy of the workbook.

> **Status: Segment 2 (desktop perception).** Window discovery, target-window
> capture, UIA inspection, and `inspect` are implemented. AI, safety, and
> automation layers are stubs.

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
| `python main.py safety test` | Segment 3 |
| `python main.py ground` | Segment 4 |
| `python main.py setup` | Segment 5 |
| `python main.py calibrate` | Segment 6 |
| `python main.py run` | Segment 7 |
| `python main.py resume` | Segment 8 |

## Safety

- `Alt+T` emergency stop, `Alt+P` pause, `Alt+R` resume (implemented in Segment 3).
- External input is blocked only while the agent is actively running.
- `Ctrl+Alt+Del` is never blocked.
- The original workbook is never modified.

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
