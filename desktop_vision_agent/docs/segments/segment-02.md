# Segment 2 - Desktop perception (window, capture, UIA)

**Version:** `v0.3-desktop`
**Status:** implemented
**Date:** 2026-11-07

## Goal

Find the manually opened target window, focus it, take a screenshot of that
window only, and list its UI Automation controls so the screenshot and UIA
coordinates can be checked for alignment.

## Deliverables

- `excel_agent/models/screen.py`
  - `Rect` (geometry, center, hit test, intersection),
  - `WindowInfo` (handle, title, process, class, window/client rects, DPI),
  - `UIACandidate` (one inspectable control).
- `excel_agent/desktop/window.py`
  - visible top-level window listing and filtering by title / process name,
  - clear "not found" and "ambiguous" errors,
  - DPI awareness set at import so UIA and screen coordinates share one space,
  - window and client rectangles in physical pixels,
  - focus with a normal `SetForegroundWindow` attempt and the
    `AttachThreadInput` fallback.
- `excel_agent/desktop/capture.py`
  - target-window capture with `PrintWindow(PW_RENDERFULLCONTENT)`,
  - `ImageGrab` fallback when `PrintWindow` fails, returns a blank image, or
    leaves black padding caused by DPI virtualization,
  - image -> screen transform (`origin`, `scale_x`, `scale_y`).
- `excel_agent/desktop/uia.py`
  - pywinauto UIA inspection limited to the target window's subtree,
  - filters for fields, buttons, labels, tabs, lists, and menus,
  - `focus`, `read_value`, and `invoke` through native UIA patterns only.
- `excel_agent/utils/images.py`
  - annotated screenshot with numbered candidate boxes.
- `python main.py inspect` with `--config`, `--max-elements`, `--no-annotate`.
- `scripts/test_form.ps1` - a native WinForms fixture titled
  "Excel Agent Test Form" with accessible TextBoxes and Save/Clear buttons.
- `config/agent.test.json` - points the agent at the fixture.
- Unit tests for `Rect`, window matching, capture transforms, DPI padding
  detection, and annotation.

## Demo

Terminal 1:

```powershell
cd desktop_vision_agent
powershell -STA -ExecutionPolicy Bypass -File scripts\test_form.ps1
```

Terminal 2:

```powershell
cd desktop_vision_agent
..\.venv\Scripts\python.exe main.py inspect --config config/agent.test.json
```

Observed:

```text
Window: 0x4308EA 'Excel Agent Test Form'
Process: powershell.exe | class: WindowsForms10.Window.8.app...
Window rect: (625, 246, 1295, 832) (670x586)
Client rect: (635, 285, 1285, 822) (650x537)
DPI: 96 (1.00x)
Screenshot: data/screenshots/inspect_...png (670x586)
UIA candidates: 15

W1  Text    Invoice Number label   ...  Invoice Number label
W2  Edit    Invoice Number         txtInvoiceNumber
W3  Text    Customer Name label    ...
W4  Edit    Customer Name          txtCustomerName
W5  Text    Amount label           ...
W6  Edit    Amount                 txtAmount
W7  Text    Notes label            ...
W8  Edit    Notes                  txtNotes
W9  Button  Save                   btnSave
W10 Button  Clear                  btnClear
W11 Text    Status                 lblStatus
...
Annotated: data/screenshots/inspect_..._annotated.png
```

The annotated screenshot was opened and checked: every box sits on its control
(label, text box, button, status label, and title-bar buttons).

## Acceptance

- Only the configured window is captured; the desktop around it is excluded.
- UIA candidates are limited to the target window.
- Candidate rectangles align with the screenshot at 100% and at 125% scaling.
- Window/client rectangles and DPI are reported.
- Ambiguous matches are refused instead of guessing.
- `pytest`: 70 passed.
- `python main.py --check`: passed.

## Decisions

- Coordinates are physical pixels. `SetProcessDpiAwareness(2)` runs when
  `desktop.window` is imported, before any geometry is read.
- `PrintWindow` is preferred because it captures occluded windows. A
  DPI-virtualized target (for example a DPI-unaware WinForms process on a 125%
  display) renders into the top-left of the bitmap and leaves black padding.
  That output is detected and discarded in favour of `ImageGrab`, which returns
  screen-aligned pixels.
- The screenshot is the whole window rectangle (title bar included), not the
  client area. UIA rectangles are also screen coordinates, so one origin and
  one scale map every candidate.
- Title matching is a case-insensitive substring; process matching accepts a
  name with or without `.exe`. Both are deliberately simple and predictable.
- Ambiguous matches are an error. Silently choosing one window could type into
  the wrong application.
- UIA inspection is read-only in this segment. `focus`, `read_value`, and
  `invoke` are available for later segments but no mouse fallback exists yet.
- The WinForms fixture is used instead of Tkinter because Tkinter exposes weak
  UIA metadata.

## Known limitations

- Custom-drawn applications may expose few or no UIA candidates; later segments
  must rely on Gemini grounding plus mouse/keyboard input for those.
- `PrintWindow` can fail for some GPU-rendered windows; the `ImageGrab` fallback
  then captures whatever is on screen at those coordinates, so the target must
  not be covered by another window.
- A minimized target is rejected; the user must restore it.
- `require_foreground` defaults to true. With it enabled, the agent brings the
  window to the front, which may interrupt other work.
- Only top-level windows are searched; dialogs inside an application are found
  through UIA inspection, not by window matching.
