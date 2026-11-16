# Excel Desktop Agent

An AI-first Windows desktop agent that takes rows out of an Excel workbook and
types them into an application you already have open — an invoicing tool, a CRM,
a legacy WinForms form, a browser window. It reads the screen once with Gemini
to learn where the fields are, then fills every row with deterministic Python,
verifies each save, and writes `DONE` / `RECHECK` back into a **copy** of the
workbook.

**The design in one line:** Gemini is the brain (it reads the screen and plans),
Python is the hands and the guardrails (it clicks, types, checks, and records).

You never edit your original workbook, and the agent never guesses that a save
worked — a row is only marked `DONE` on positive evidence.

> **Status:** v1.0.0. 10 build segments plus hardening (save confirmation,
> crash recovery, run locking, data cleanup). 515 unit tests and 7 integration
> tests.

---

## Contents

- [Who this is for](#who-this-is-for)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Verify the install](#verify-the-install)
- [Your first run (10 minutes, safe)](#your-first-run-10-minutes-safe)
- [Three ways to use it](#three-ways-to-use-it)
- [Commands](#commands)
- [How your data is stored](#how-your-data-is-stored)
- [Configuration](#configuration)
- [Safety, stopping and recovering](#safety-stopping-and-recovering)
- [AI usage and cost](#ai-usage-and-cost)
- [Testing and the offline demo](#testing-and-the-offline-demo)
- [Troubleshooting](#troubleshooting)
- [Implemented vs. not supported yet](#implemented-vs-not-supported-yet)
- [Project layout](#project-layout)
- [FAQ](#faq)

---

## Who this is for

Anyone who has a spreadsheet full of records and a business application with no
API — the kind where someone would otherwise retype the same 200 rows. You need
to be comfortable with a terminal for setup, but once configured you run the
wizard and press a key.

It is **not** a web scraper, a cloud service, or a RPA suite. It is one Python
program on one Windows machine acting on a window you opened yourself.

---

## How it works

```text
  Excel workbook (read-only input)
            |
            |  copy once
            v
  working copy  --->  status columns written back after every row
            |
   1. Ground   : one screenshot + UIA control list + your Excel headers
                 -> Gemini returns field positions and buttons
   2. Map      : column <-> form field, validated, approved by you
   3. Calibrate: fill row 1, read it back, ask Gemini to confirm the values,
                 click Save, then read the success region
   4. Run      : every remaining row uses the same deterministic path;
                 one Gemini text read per saved row (or none, if configured)
   5. Record   : DONE / RECHECK + timestamp + error text in the working copy
```

Three properties come out of that split:

- **Cheap at scale.** The model is called once per layout, not once per field or
  per row. A 200-row batch costs one grounding call plus one confirmation read
  per saved row (and that can be set to zero).
- **Predictable.** Clicking and typing are pure Python. The same mapping always
  produces the same actions.
- **Fail-closed.** If a save cannot be proven, the row becomes `RECHECK`, is
  never re-saved, and the run stops by default.

There are two pipelines, and they answer different questions:

|                  | Mapped pipeline (`setup` → `calibrate` → `run`) | Step pipeline (`form run`)               |
| ---------------- | ------------------------------------------------------- | ------------------------------------------ |
| Question         | *Which field does each column go into?*               | *In what order do I click and type?*     |
| Needs Gemini     | Yes, for grounding                                      | Only if you verify saves with text         |
| Stores a mapping | Yes, `data/state/mapping.json`                        | No, the plan is inline in the config       |
| Order of actions | Derived from the grounded field map                     | Literal list of `click` / `fill` steps |
| Good for         | One screen, many inputs                                 | Add → fill → Save forms                  |

---

## Requirements

|            |                                                                      |
| ---------- | -------------------------------------------------------------------- |
| OS         | Windows 11 (developed and tested on Windows 11)                      |
| Python     | 3.11 or newer (developed and tested on 3.12.2)                       |
| Account    | A Google Gemini API key — free tier is enough for a demo            |
| Target app | Already open, maximized or restored, **not** minimized         |
| Display    | One screen, 100%–200% scaling; the agent handles DPI virtualization |

There is no database, no server, no login and no cloud component other than the
Gemini API call itself.

---

## Install

All commands run from the project folder (the folder containing `main.py`).

### 1. Open a terminal in the project folder

PowerShell:

```powershell
cd path\to\exel_automaiton\desktop_vision_agent
```

Command Prompt / Git Bash: same idea — `cd` to the folder with `main.py` in it.

### 2. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Git Bash equivalent:

```bash
python -m venv .venv
source .venv/Scripts/activate
```

If PowerShell blocks the activate script, either run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for this window
only, or skip activation and call the interpreter directly:
`.\.venv\Scripts\python.exe main.py --check`.

### 3. Install the dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

That installs `pydantic`, `python-dotenv`, `pandas`, `openpyxl`,
`google-genai`, `pillow`, `pywin32`, `comtypes`, `pywinauto`, `pyperclip` and
`pyautogui`. The Windows automation packages (`pywin32`, `comtypes`,
`pywinauto`, `pyautogui`) only install properly on Windows.

For running the tests as well:

```powershell
python -m pip install pytest pytest-cov
```

### 4. Create your `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Set the key, nothing else is required:

```dotenv
GEMINI_API_KEY=your-key-here
```

Get a key from [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey). Optional variables:
`GEMINI_MODEL` (default `gemini-3.6-flash`), `LOG_LEVEL`, `LOG_CONSOLE=1` to also
echo the log to the terminal.

`.env` is gitignored. The agent never prints the key, and every logged prompt or
response is credential-redacted.

---

## Verify the install

```powershell
python main.py --check
```

It prints one line per requirement and ends with `Result: PASSED`:

```text
[ OK ] Platform: Windows 11 (AMD64)
[ OK ] Python: 3.12.2 (requires >= 3.11)
[ OK ] dependency: pydantic: configuration models - 2.14.3
...
[ OK ] .env file: C:\...\desktop_vision_agent\.env
[ OK ] GEMINI_API_KEY: loaded (AIza...abcd)
[ OK ] config: config/agent.example.json - 3 field mapping(s)
[ OK ] data/output writable: C:\...\data\output
Result: PASSED - environment is ready
```

A missing API key, a missing dependency, an unwritable `data/` folder or a bad
config file each show up as `[FAIL]` with the fix spelled out. `--check` never
calls Gemini and costs nothing.

```powershell
python main.py --version
```

---

## Your first run (10 minutes, safe)

Before pointing the agent at a real business app, drive it against the bundled
test form. This exercises the whole pipeline against a window designed for it.

### Step 1 — a sample workbook

```powershell
python scripts/make_sample_excel.py
```

Writes `data/input/invoices.xlsx` with a header row, five sample rows (one is
already marked `DONE`, so four are pending) and the status columns the agent
needs (`Agent Status`, `Agent Timestamp`, `Agent Error`). Regenerate it any
time; it is safe to overwrite.

### Step 2 — open the test form

In a **second** terminal:

```powershell
python scripts/test_form.py
```

A small Tkinter window titled **“Excel Agent Test Form”** appears with Invoice
Number, Customer Name, Amount, Notes, Save, Clear and a status label. Leave it
open and in front when the agent asks for it.

### Step 3 — prove the safety lock works

```powershell
python main.py safety test --seconds 10
```

Your mouse and keyboard are blocked for 10 seconds; press `Alt+T` to release
them early. This is the same lock the agent uses, so you know the escape hatch
before automation touches your machine.

### Step 4 — let the wizard drive it

```powershell
python main.py --agent
```

Answer the prompts:

1. Pick **Excel Agent Test Form** from the open-window list.
2. Excel path: type `data/input/invoices.xlsx`.
3. Choose **1. Single step** for the mapped pipeline.
4. Review the proposed mapping (Invoice Number → Invoice Number, etc.) and
   accept it.
5. Watch the first row get filled and saved, and check the calibration result
   it prints.
6. Answer **y** to “Do you want to start automation?”. The sample has only four
   pending rows, so it finishes quickly.

The terminal shows every action and the outcome of each row. When it finishes,
open `data/output/` — there is a copy of the workbook with `DONE` written for
each saved row. Your original in `data/input/` is untouched.

### Step 5 — check what it left behind

```powershell
python main.py clear-data --all --dry-run
```

Prints how many files and how many MB each category would remove. When you are
ready for a clean slate:

```powershell
python main.py clear-data --all
```

`--all` deliberately keeps `data/input` (your source workbooks) and
`data/state/mapping.json` (the expensive-to-recreate mapping).

From here, run the same wizard against your own application and your own
workbook.

---

## Three ways to use it

### 1. Interactive wizard — recommended

```powershell
python main.py --agent
```

The wizard does the whole setup for you and writes a config file:

1. Lists open windows; pick or search your application.
2. Asks for your Excel file, verifies the sheet, detects the header row.
3. Asks **single step** or **multi step**.
4. *Single step*: generates a config, grounds the layout, shows you the mapping
   for approval, then calibrates on the first row.
5. *Multi step*: you describe each step in plain words (“click Add New”,
   “fill up all the form”); the wizard turns each into clicks and fills, shows
   what it understood, and does it live on the first row.
6. Prompts *“Do you want to start automation?”* and runs the batch.

The same wizard in a floating window, when the terminal would cover your app:

```powershell
python main.py --agent-ui
```

Drag it by the title bar; `▾` collapses it, `◑` cycles opacity, `✕` closes it.
Answers go in the text box (Enter or **Send**); a blank answer accepts the
default. It never steals focus from your application, which is why it never
pauses before each save — confirm-per-save is a terminal feature.

### 2. Static step plan — `form run`

For forms where the order of actions is the *whole* problem: click “Add”, type
three boxes, click Save. Describe the sequence in the config and there is no
mapping, no calibration and no run state:

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

```powershell
python main.py form run --config config/agent.steps.example.json --limit 5
```

Only `click` and `fill` exist. A `fill` uses the label from `fields`; add
`"field": "Other Label"` when the control is named differently, and
`"optional": true` when a missing control should be a note rather than a
failure. The window is re-read after every click, so controls created by a click
are visible to the steps after it. Every fill is read back before Save.

`form run` clicks Save last. It does not open the entry form for you, so a
plan for a screen that is not already showing the inputs starts with a `click`
step for the button that opens it. (`new_record_button` is accepted in the
config but is not used by the current runtime.)

### 3. Mapped pipeline — the explicit route

Four commands, each of which you can inspect before going on:

```powershell
python main.py ground    --config config/agent.test.json   # read the screen
python main.py setup     --config config/agent.test.json   # approve the mapping
python main.py calibrate --config config/agent.test.json   # prove row 1
python main.py run       --config config/agent.test.json --limit 5
```

- `ground` sends one screenshot, the UIA control list and your headers to
  Gemini, validates the returned map, and writes
  `data/grounding/grounding_<stamp>.json` plus an annotated screenshot
  (red = fields, blue = buttons). One API call.
- `setup` grounds, then opens an approval editor: `set <column> <field>`,
  `remove <column>`, `show`, `done`, `cancel`. Approval is refused while
  validation errors exist (unknown/duplicate column or field, type mismatch,
  non-numeric value in a number column, missing required column).
  Low confidence is a warning and needs an explicit approval. `--yes` approves
  non-interactively. Saves `data/state/mapping.json`.
- `calibrate` processes the first pending row: fills, reads back through UIA,
  takes a pre-save screenshot, asks Gemini to confirm the values, clicks Save,
  then reads the configured success/error region. The map only becomes
  `trusted: true` on `CONFIRMED`.
- `run` refuses to start while the mapping is untrusted — there is no override
  flag. Use `--confirm-each` to approve each Save by hand while you watch.

---

## Commands

Run everything from the project folder. `--config` defaults to
`config/agent.example.json` and `--env-file` defaults to `.env`.

| Command                                                  | What it does                                        | API calls    |
| -------------------------------------------------------- | --------------------------------------------------- | ------------ |
| `python main.py --check`                               | Environment / dependency / config check             | 0            |
| `python main.py --version`                             | Print version                                       | 0            |
| `python main.py --agent`                               | Full interactive wizard                             | as needed    |
| `python main.py --agent-ui`                            | Same wizard in a floating window                    | as needed    |
| `python main.py agent`                                 | Same wizard, subcommand form                        | as needed    |
| `python main.py excel inspect --limit 20`              | Show workbook rows and pending count                | 0            |
| `python main.py inspect`                               | Screenshot + list UIA controls of the target window | 0            |
| `python main.py safety test --seconds 10`              | Exercise the input lock and hotkeys                 | 0            |
| `python main.py ground`                                | Ground the layout with Gemini                       | 1            |
| `python main.py setup [--yes]`                         | Ground + approve/save the mapping                   | 1            |
| `python main.py calibrate`                             | Fill, verify and save the first pending row         | 2            |
| `python main.py run [--limit N] [--confirm-each]`      | Batch the pending rows                              | 0–1 per row |
| `python main.py resume`                                | Continue an interrupted run                         | 0–1 per row |
| `python main.py form run [--limit N] [--confirm-each]` | Run a static step plan                              | 0–1 per row |
| `python main.py clear-data [flags]`                    | Delete runtime data from previous runs              | 0            |

Useful flags across the pipeline: `--mapping <path>` (default
`data/state/mapping.json`), `--state <path>` (default
`data/state/run_state.json`), `--no-overlay` (hide the status overlay),
`--no-annotate` (skip the annotated screenshot on `inspect`/`ground`/`setup`).

### `clear-data`

Prints a count and size per category and asks before deleting anything.

| Flag                          | Removes                                                                                                                                                                                 |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--scrn`, `--screenshots` | images under `screenshots`, `setup`, `grounding`, `steps`, `annotated` (JSON plans stay)                                                                                      |
| `--ai-logs`                 | `data/ai_logs` prompt/response logs                                                                                                                                                   |
| `--logs`                    | `data/logs`; an `agent.log` still open is emptied instead of deleted                                                                                                                |
| `--output`                  | working copies in `data/output` **and** `data/state/output.json`                                                                                                              |
| `--state`                   | `run_state.json`, `run_state.stale-*`, `pending_status.json`, `output.json`, `run.lock`, `state/backup_*` (keeps `mapping.json` and your `demo_*` / `recipe_*` files) |
| `--mapping`                 | `data/state/mapping.json` — run `setup` again afterwards                                                                                                                           |
| `--input`                   | source workbooks in `data/input`                                                                                                                                                      |
| `--backup`                  | `data/backup` archives                                                                                                                                                                |
| `--cache`                   | `__pycache__` and `.pytest_cache` folders                                                                                                                                           |
| `--configs`                 | generated `config/agent.interactive*.json` and `config/agent.run*.json`                                                                                                             |
| `--all`                     | screenshots, AI logs, logs, output, state, backups — **not** `--input`, **not** `mapping.json`                                                                         |

`--dry-run` only previews, `--yes` / `-y` skips the prompt, and `--force`
clears state even when `pending_status.json` still has queued statuses or
`run.lock` shows a live run. Files held open elsewhere are reported as
`skipped` with a non-zero exit code; `.gitkeep` files always survive.

Clearing `--output` means every row is processed again (the input is never
modified). Clearing `--state` resets progress and run history.

---

## How your data is stored

Everything the agent produces lives under `data/` and is gitignored. The main
folders exist in the repo via `.gitkeep` files; `backup/` and `steps/` are
created on demand. `clear-data` never removes a `.gitkeep`.

| Path                               | What is in it                                        |
| ---------------------------------- | ---------------------------------------------------- |
| `data/input/`                    | your source workbooks (never modified)               |
| `data/output/`                   | working copies, one per input, statuses written here |
| `data/state/output.json`         | which working copy the current run is using          |
| `data/state/mapping.json`        | approved column → field map + `trusted` flag      |
| `data/state/run_state.json`      | run progress, write-ahead save intent                |
| `data/state/pending_status.json` | statuses that could not be written to Excel yet      |
| `data/state/run.lock`            | pid of the run currently driving the app             |
| `data/screenshots/`              | pre- and post-save screenshots, one pair per attempt |
| `data/grounding/`                | grounding JSON + annotated layout screenshot         |
| `data/setup/`, `data/steps/`   | wizard step plans, per-timestamp screenshots         |
| `data/ai_logs/`                  | every prompt and response, credentials redacted      |
| `data/logs/agent.log`            | the log every command appends to                     |
| `data/annotated/`                | annotated window captures from `inspect`           |

The working copy is chosen once and remembered in `output.json`. `calibrate`
and `run` reuse that same copy — that is how a calibrated row is skipped by the
batch runner. Delete `output.json` to start a fresh copy. If the recorded copy
is missing, the agent **refuses to start** rather than silently re-copying the
input, because that would lose every status and risk duplicate records.

The workbook is saved after every row, so stopping mid-run loses nothing.

---

## Configuration

Start from `config/agent.example.json` (blank template), `config/agent.test.json`
(tuned for the bundled test form), or `config/agent.steps.example.json` (step
plan). The wizard writes `config/agent.interactive.json` /
`config/agent.interactive.steps.json` for you.

| Block            | Purpose                                                                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app`          | `title_contains` (substring of the window title), optional `process_name`, `require_foreground`                                                                                            |
| `excel`        | input/output paths, sheet, `header_row`, id column, status columns, `done_value` / `recheck_value`, `skip_statuses`, `empty_cell_policy` (`skip` or `clear`), `required_columns` |
| `fields`       | the column → form-field pairs: `column`, `field`, and `type` (`text` or `number`)                                                                                                             |
| `actions`      | `save_button`, `wait_after_save_seconds` (`new_record_button` is reserved, currently unused)                                                                                              |
| `gemini`       | `model`, `max_image_dimension`, `timeout_seconds`, `max_retries`, `max_ai_calls` (default 50), `min_layout_confidence`, `min_mapping_confidence`                                   |
| `confirmation` | normalized 0–1000 `success_region` / `error_region`, `success_pattern`, `error_patterns`, optional `verifier` and `footprint`                                                       |
| `safety`       | hotkeys, `block_external_input`, `max_attempts`, `action_delay_seconds`, `stop_on_failure`                                                                                               |
| `form`         | step plan for `form run`: `steps`, `pause_after_click_seconds`, `retry_on_failure`                                                                                                       |
| `scrolling`    | `enabled`, `max_screens`, `settle_seconds`, `wheel_notches` — used when the new row lands below the fold                                                                                |

Rules the config loader enforces, so you find out at load time rather than
mid-run:

- the status, timestamp and error columns must not collide with a mapped column;
- a column can map to a field only one-to-one (`_mapping_is_one_to_one`);
- every `fill` step's column must be declared in `fields`;
- confirmation patterns must be valid regular expressions.

### What counts as a saved row

The `confirmation` block decides. Regions are in 0–1000 normalized window
coordinates, so they survive window moves and scaling. After Save, the agent
crops the region and reads its text through an injectable `text_reader`
(Gemini is the only real reader today).

| Outcome       | Meaning                       | Row status                                |
| ------------- | ----------------------------- | ----------------------------------------- |
| `CONFIRMED` | `success_pattern` matched   | `DONE`                                  |
| `REJECTED`  | an `error_patterns` matched | `RECHECK`                               |
| `UNKNOWN`   | no usable evidence            | `RECHECK`, *record may already exist* |

Pixel difference between the pre- and post-save screenshots is a **weak hint in
the log only**; it can never confirm a save. If no confirmation pattern is
configured, every saved row falls back to `RECHECK`.

For apps that show no message at all, `"confirmation": {"verifier": "row"}`
re-reads the window after the click and looks for a value from the saved row
appearing more often than before, scrolling down if needed — purely local, no
AI call. `"verifier": "text"` uses Gemini on the configured region.
`"verifier": "none"` skips the check entirely: every row is marked `DONE`
without looking at the screen, and a save the app refused will not be noticed.
The run prints a warning and logs `save check is off (verifier=none)`.

---

## Safety, stopping and recovering

### Hotkeys

| Key       | Action                                                    |
| --------- | --------------------------------------------------------- |
| `Alt+T` | emergency stop, releases the input lock immediately       |
| `Alt+P` | pause between actions                                     |
| `Alt+R` | resume; the target window is re-fronted and re-read first |

External input is blocked only while the agent is actively running, the hooks
are released by a background watchdog if the process dies or hangs, and
`Ctrl+Alt+Del` is preserved at the OS level as an unblockable escape.

Every click is checked against the live window immediately before it is sent:
the point must still be inside the target window and land on the app itself, not
on whatever came in front. Otherwise nothing is clicked and the row stops with
*“nothing was clicked: … is not over … any more”*. A stale coordinate can never
click another window. Pausing and closing the form leads to `RECHECK`, not to a
click at a remembered point.

### Never a duplicate save

- Save is clicked **once** per row. If a save may have happened, the row is not
  retried automatically — it becomes `RECHECK` with *“interrupted around Save;
  verify in target app”*.
- Retries only cover work **before** the click: fill, UIA read-back, or a stop.
- Immediately before each Save click the agent writes a write-ahead intent
  (`{row_number, invoice_id, state: "attempting_save", ts}`) and flushes it to
  disk. `resume` finds it and refuses to re-save.
- `run` refuses to touch the working copy while such a marker is unresolved; it
  asks you to `resume` first.

### `stop_on_failure`

`safety.stop_on_failure` defaults to `true`: the first non-`DONE` row stops the
run before the next row. Set it to `false` only when you deliberately want to
continue past `RECHECK` rows.

### Stop and resume

Press `Alt+T` mid-run. Finished rows keep their `DONE`; the row in progress has
no status, so resuming restarts it from scratch instead of saving it twice.

```powershell
python main.py resume --config config/agent.test.json
```

`resume` refuses to start unless the config, mapping, input workbook and working
copy all match the recorded run. It reuses the exact working copy (repairing
`output.json` if needed), re-screenshots, re-inspects UIA and re-resolves the
map. If the layout changed structurally it stops and asks for `setup` again —
it never re-grounds unattended.

A run that can never be resumed (config, mapping or input changed, or the map
was re-approved) is moved aside as `run_state.stale-<timestamp>.json` and `run`
starts a fresh batch on the copy that run was using. A run whose working copy is
missing blocks instead: restore the copy, or delete the run state and
`output.json` to start over, accepting that saved rows may be processed again.

### If Excel refuses the write

The outcome is retried with backoff. If it still fails, the run stops with a
clear message and the outcome is stored in both `run_state.json` and
`pending_status.json`. The next `run` or `resume` replays the pending statuses
before reading any row, so a saved outcome is never lost and never re-saved.

### One run at a time

`data/state/run.lock` holds the owner pid; a second run refuses to start with
*“another run is already active (pid …)”* instead of interleaving clicks and
racing on the workbook. A lock left by a killed process is taken over
automatically (the pid is checked) and the file is removed when the run ends.

---

## AI usage and cost

The model is `gemini-3.6-flash`, and `gemini.max_ai_calls` is a hard ceiling
(default 50) — the agent stops rather than silently overspending.

| Action                                                | Calls                                                  |
| ----------------------------------------------------- | ------------------------------------------------------ |
| `ground` / `setup`                                | 1 per layout                                           |
| `calibrate`                                         | 1 pre-save value check + 1 post-save confirmation read |
| each batch row with `verifier: "text"`              | 1                                                      |
| each batch row with `verifier: "row"` or `"none"` | 0                                                      |
| each wizard sentence turned into steps                | 1                                                      |
| `form run` with `verifier: "none"`                | 0                                                      |

So a 100-row batch after calibration costs about 100 calls with text
verification, and 0 with local verification. Every call and response is logged
to `data/ai_logs/` with credentials redacted if you need to audit one.

Nothing else leaves the machine. There are no webhooks, no telemetry, no
licence check. The only outbound traffic is `google-genai` talking to Gemini;
the rest is files, plus a `threading.Condition` between the safety layer and the
runner.

---

## Testing and the offline demo

Unit tests (no window, no API key needed):

```powershell
python -m pytest
python -m pytest -v
```

515 unit tests are collected, plus 7 integration tests in three modules under
`tests/integration/` (`test_capture`, `test_uia`, `test_fixture_form`). The
integration tests drive real windows and the Tkinter fixture; run them with the
test form available and expect them to be slower and environment-sensitive:

```powershell
python -m pytest tests/integration -v
```

To see the whole lifecycle — grounding, mapping, calibration, batch run, resume
— with **zero** API usage and mocked Gemini responses:

```powershell
python scripts/demo_offline.py
```

It prepares `data/input/invoices.xlsx`, writes `data/state/mapping.json`, runs
calibration and the batch, then prints the final workbook status and run
summary. `--no-clean` preserves the existing state and output files instead of
starting from a clean slate. This is the best thing to show someone who wants to
understand the project without handing over an API key.

---

## Troubleshooting

**The wizard says no windows match.**
`app.title_contains` must be a substring of the visible title bar. Minimized
windows cannot be captured — restore the window. Use `python main.py inspect` to
see what the agent can actually see.

**Coordinates are off / clicks land in the wrong place.**
Windows display scaling (125%, 150%) can misalign a target that is DPI-unaware.
The agent compensates for DPI virtualization padding and runs PerMonitorV2
aware, but if the app itself reports wrong rectangles, try 100% scaling and re-run
`inspect`. The same applies to custom fixtures.

**A button is not found.**
UIA names must match the config exactly, including spelling and punctuation.
Open `data/annotated/` and the grounding JSON to see the names the agent read.
Press `r` in the wizard to re-read the screen after fixing the app state.

**A row ended `RECHECK` with “record may already exist”.**
The save clicked but no confirmation evidence was found — usually a wrong
`success_region` or `success_pattern`. Look at the post-save screenshot in
`data/screenshots/`, fix the confirmation block, and check the app by hand for
that row. The row will not be re-saved.

**Excel is open and the agent cannot write.**
Close the workbook in Excel. The agent writes the working copy in
`data/output/`; if Excel holds it, the status retries with backoff and then
queues into `pending_status.json`.

**`another run is already active (pid …)`.**
A run is live, or a killed run left a lock. Confirm the pid is gone, then delete
`data/state/run.lock` (or let the agent take it over — it checks the pid).

**I blocked my own mouse and keyboard.**
`Alt+T` releases the lock. `Ctrl+Alt+Del` always works.

**Nothing is happening on screen.**
The console prints every action, and `data/logs/agent.log` has the same story
with timings. Watch that file rather than the screen. Set `LOG_CONSOLE=1` to
mirror the log into the terminal.

**A save happened but the workbook says `RECHECK`.**
That is the fail-closed path working: the agent could not prove the save, so it
marked the row for a human. Verify the record in the app, correct the status
column, and continue.

---

## Implemented vs. not supported yet

| Feature                         |   Status   | Notes                                                      |
| ------------------------------- | :---------: | ---------------------------------------------------------- |
| Text and number inputs          | Implemented | UIA + SendInput with clipboard fallback                    |
| Single-window native forms      | Implemented | The main supported case                                    |
| Browser forms                   | Implemented | UIA names from Chrome/Edge/Brave                           |
| Visual grounding                | Implemented | Gemini, one call per layout                                |
| First-row calibration           | Implemented | Pre-save value check + post-save confirmation              |
| Deterministic batch entry       | Implemented | 0–1 Gemini read per saved row                             |
| Config-driven save confirmation | Implemented | Text, local row check, or off                              |
| Stop and resume                 | Implemented | Write-ahead intent + `run_state.json`                    |
| Pending status replay           | Implemented | `pending_status.json` sidecar                            |
| Data cleanup                    | Implemented | `clear-data` with per-category plans                     |
| Step plans (`click`/`fill`) | Implemented | `form run`, plus the multi-step wizard                   |
| Dropdowns / comboboxes          |  Deferred  | Planned; use the mapped pipeline and fill by hand for now  |
| Checkboxes / radios             |  Deferred  | Planned                                                    |
| Multi-tab / wizard screens      |  Deferred  | Planned                                                    |
| Automatic app launch            |  Deferred  | You open the target window yourself                        |
| OCR text reader                 |  Deferred  | `TextReader` interface exists; Gemini is the only reader |
| Pages that scroll as a whole    |  Deferred  | The local save check scrolls the grid, not the window      |

---

## Project layout

```text
desktop_vision_agent/
├── main.py                  # thin entrypoint: setup logging, call the CLI
├── excel_agent/
│   ├── cli.py               # every command and flag
│   ├── config.py            # pydantic config models + validation
│   ├── agent/               # orchestrator, wizard, calibration, steps, state,
│   │                        # run lock, cleanup, chat UI, row search/evidence
│   ├── ai/                  # Gemini client, grounding, mapping, prompts
│   ├── desktop/             # window capture, UIA, input, hotkeys, overlay
│   ├── excel/               # workbook read/write, status columns
│   ├── safety/              # input lock, pause guard, controller
│   ├── models/              # shared data models (actions, rects, rows)
│   └── utils/               # small helpers (paths, timing, text)
├── config/                  # agent.example.json, agent.test.json,
│                            # agent.steps.example.json, prompts/
├── data/                    # runtime input/output/state/logs (gitignored)
├── docs/                    # architecture .mmd diagrams + segments/00-10
├── scripts/                 # test_form.py, make_sample_excel.py,
│                            # demo_offline.py
├── tests/                   # unit/ and integration/
├── plan.md                  # full architecture and segment roadmap
├── CHANGELOG.md             # one entry per segment
└── requirements.txt
```

---

## FAQ

**Will it overwrite my spreadsheet?**
No. It copies your input to `data/output/` once and writes statuses only to that
copy.

**Can I run it on a machine with no internet?**
Everything except grounding and text-based save confirmation. Run `calibrate`
and `setup` once while online, then use `verifier: "row"` (or `"none"`) for
batch runs to keep them local. `scripts/demo_offline.py` shows the fully mocked
path.

**Can two people run it at once?**
Not on the same workbook and window. The run lock stops a second run; separate
copies of the project can run in parallel.

**What if my app is browser-based and the layout changes?**
The mapped pipeline stops when the layout changes structurally and asks for
`setup` again, rather than clicking blind. That is deliberate.

**Where do I look when something went wrong?**
`data/logs/agent.log` first, then `data/screenshots/` for the visual state, then
`data/ai_logs/` if the model returned something unexpected.

---

See `plan.md` for the full architecture and the 10-segment roadmap, and
`CHANGELOG.md` for what changed in each segment.
