"""Command-line entrypoint and environment diagnostics.

Implemented: ``--check``, ``--version``, ``excel inspect`` (Segment 1),
``inspect`` (Segment 2), and ``safety test`` (Segment 3). The remaining
subcommands are registered now so the CLI shape is stable; each one reports
which segment will implement it.
"""

from __future__ import annotations

import argparse
import importlib
import os
import platform
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config, load_env, project_root
from .logging_setup import mask_secret, setup_logging

_REQUIRED_MODULES = (
    ("pydantic", "configuration models"),
    ("dotenv", "environment loading"),
    ("pandas", "Excel reading"),
    ("openpyxl", "Excel writing"),
    ("PIL", "screenshot handling"),
    ("google.genai", "Gemini client"),
    ("win32gui", "window capture"),
    ("comtypes", "UI Automation"),
    ("pywinauto", "UIA helper"),
    ("pyperclip", "clipboard paste"),
)

_FUTURE_COMMANDS = {
    "setup": "Segment 5 (mapping & approval)",
    "calibrate": "Segment 6 (first-row calibration)",
    "run": "Segment 7 (batch row runner)",
    "resume": "Segment 8 (stop/resume & state)",
    "ground": "Segment 4 (Gemini grounding)",
}


@dataclass
class Check:
    """One line in the environment report."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    critical: bool = True


def _import_check(module_name: str) -> tuple[bool, str]:
    try:
        with warnings.catch_warnings():
            # Third-party packages (notably pywinauto) warn on import; the
            # diagnostic report should stay clean.
            warnings.simplefilter("ignore")
            module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - report any import problem
        return False, f"{type(exc).__name__}: {exc}"
    version = getattr(module, "__version__", "")
    return True, str(version) if version else "imported"


def _writable_check(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"{directory}: {exc}"
    return True, str(directory)


def _collect_checks(config_path: str, env_path: str | None) -> list[Check]:
    checks: list[Check] = []

    checks.append(
        Check(
            "Platform",
            "ok" if sys.platform == "win32" else "fail",
            f"{platform.system()} {platform.release()} ({platform.machine()})",
        )
    )

    version_ok = sys.version_info >= (3, 11)
    checks.append(
        Check(
            "Python",
            "ok" if version_ok else "fail",
            f"{platform.python_version()} (requires >= 3.11)",
        )
    )

    for module_name, purpose in _REQUIRED_MODULES:
        ok, detail = _import_check(module_name)
        checks.append(
            Check(
                f"dependency: {module_name}",
                "ok" if ok else "fail",
                f"{purpose} - {detail}",
            )
        )

    env_file = load_env(env_path)
    if not env_file.is_file():
        checks.append(
            Check(
                ".env file",
                "fail",
                f"missing {env_file} (copy .env.example and add GEMINI_API_KEY)",
            )
        )
    else:
        checks.append(Check(".env file", "ok", str(env_file)))

    key = os.getenv("GEMINI_API_KEY", "").strip()
    checks.append(
        Check(
            "GEMINI_API_KEY",
            "ok" if key else "fail",
            f"loaded ({mask_secret(key)})" if key else "not set",
        )
    )

    config = None
    try:
        config = load_config(config_path)
        checks.append(
            Check(
                "config",
                "ok",
                f"{config_path} - {len(config.fields)} field mapping(s)",
            )
        )
    except ConfigError as exc:
        checks.append(Check("config", "fail", str(exc)))

    if config is not None:
        checks.append(
            Check(
                "Gemini model",
                "ok" if config.gemini.model else "fail",
                config.gemini.model,
            )
        )
        checks.append(
            Check(
                "hotkeys",
                "ok",
                f"stop={config.safety.stop_hotkey} "
                f"pause={config.safety.pause_hotkey} "
                f"resume={config.safety.resume_hotkey}",
                critical=False,
            )
        )

    for name in ("logs", "output", "screenshots", "state"):
        ok, detail = _writable_check(project_root() / "data" / name)
        checks.append(
            Check(
                f"data/{name} writable",
                "ok" if ok else "fail",
                detail,
                critical=False,
            )
        )

    git_dir = project_root().parent / ".git"
    checks.append(
        Check(
            "git repository",
            "ok" if git_dir.is_dir() else "warn",
            str(git_dir) if git_dir.is_dir() else "not initialised",
            critical=False,
        )
    )

    return checks


def _print_checks(checks: list[Check]) -> int:
    print(f"Excel Desktop Agent v{__version__} - environment check")
    print("-" * 72)
    for check in checks:
        label = {"ok": " OK ", "warn": "WARN", "fail": "FAIL"}[check.status]
        print(f"[{label}] {check.name}: {check.detail}")
    print("-" * 72)

    failures = [c for c in checks if c.status == "fail"]
    warnings = [c for c in checks if c.status == "warn"]
    critical = [c for c in failures if c.critical]

    if critical:
        print(f"Result: FAILED - {len(critical)} critical issue(s)")
        return 1
    if failures:
        print(f"Result: PASSED with {len(failures)} non-critical issue(s)")
        return 0
    if warnings:
        print(f"Result: PASSED with {len(warnings)} warning(s)")
        return 0
    print("Result: PASSED - environment is ready")
    return 0


def run_check(config_path: str, env_path: str | None = None) -> int:
    """Run the Segment 0 environment check."""
    setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
    return _print_checks(_collect_checks(config_path, env_path))


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _safe_format(value, field_type: str) -> str:
    from .excel.reader import format_value

    try:
        return format_value(value, field_type)
    except ValueError as exc:
        return f"<invalid: {exc}>"


def run_excel_inspect(config_path: str, limit: int = 20) -> int:
    """Read the workbook and print its rows without writing anything."""
    from .excel.reader import (
        ExcelReadError,
        pending_rows,
        read_workbook,
        validate_pending_rows,
    )

    try:
        config = load_config(config_path)
        workbook = read_workbook(config)
    except (ConfigError, ExcelReadError) as exc:
        print(f"ERROR: {exc}")
        return 1

    pending = pending_rows(workbook, config)
    pending_numbers = {row.row_number for row in pending}
    issues = validate_pending_rows(pending, config)

    print(f"Workbook: {workbook.path}")
    print(
        f"Sheet: {workbook.sheet_name} | header row: {config.excel.header_row} "
        f"| columns: {len(workbook.headers)}"
    )
    print("Headers: " + ", ".join(workbook.headers))
    print()
    print(f"{'Row':>5}  {'ID':<16} {'Status':<10} {'Pending':<8} Values")
    print("-" * 100)

    shown = workbook.rows if limit <= 0 else workbook.rows[:limit]
    for row in shown:
        id_text = _safe_format(row.values.get(config.excel.id_column), "text")
        if row.is_blank():
            pending_label = "blank"
        elif row.row_number in pending_numbers:
            pending_label = "yes"
        else:
            pending_label = "skip"
        values = " | ".join(
            f"{field.field}={_safe_format(row.values.get(field.column), field.type)}"
            for field in config.fields
        )
        print(
            f"{row.row_number:>5}  {_truncate(id_text, 16):<16} "
            f"{_truncate(row.status or '-', 10):<10} {pending_label:<8} "
            f"{_truncate(values, 80)}"
        )

    if limit > 0 and len(workbook.rows) > limit:
        print(f"... {len(workbook.rows) - limit} more row(s); use --limit 0 to show all")

    print("-" * 100)
    print(
        f"Rows: {len(workbook.rows)} | pending: {len(pending)} "
        f"| skipped: {len(workbook.rows) - len(pending)}"
    )
    if issues:
        print(f"Validation issues ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("Validation: OK")
    return 0


def run_inspect(config_path: str, annotate: bool = True, max_elements: int = 300) -> int:
    """Capture the target window and list its UIA controls."""
    from .desktop.capture import CaptureError, capture_window
    from .desktop.uia import UIAError, inspect_window
    from .desktop.window import WindowError, attach_window
    from .utils.images import annotate_candidates

    try:
        config = load_config(config_path)
        window = attach_window(config.app)
        capture = capture_window(window)
    except (ConfigError, WindowError, CaptureError) as exc:
        print(f"ERROR: {exc}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_dir = project_root() / "data" / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    raw_path = screenshot_dir / f"inspect_{stamp}.png"
    capture.image.save(raw_path)

    print(f"Window: 0x{window.handle:X} {window.title!r}")
    print(f"Process: {window.process_name or 'unknown'} | class: {window.class_name}")
    print(
        f"Window rect: {window.rect.bbox} "
        f"({window.rect.width}x{window.rect.height})"
    )
    print(
        f"Client rect: {window.client_rect.bbox} "
        f"({window.client_rect.width}x{window.client_rect.height})"
    )
    print(f"DPI: {window.dpi} ({window.dpi_scale:.2f}x)")
    print(f"Screenshot: {raw_path} ({capture.image.width}x{capture.image.height})")

    try:
        inspection = inspect_window(window, max_elements=max_elements)
    except UIAError as exc:
        print(f"ERROR: {exc}")
        print("Screenshot saved for diagnosis.")
        return 1

    print(f"UIA candidates: {len(inspection.candidates)}")
    print(
        f"{'ID':<5} {'Type':<12} {'Name':<30} {'Automation ID':<22} "
        f"{'Rect':<26} Value"
    )
    print("-" * 120)
    for candidate in inspection.candidates:
        print(
            f"{candidate.candidate_id:<5} {candidate.control_type:<12} "
            f"{_truncate(candidate.name, 30):<30} "
            f"{_truncate(candidate.automation_id, 22):<22} "
            f"{str(candidate.rect.bbox):<26} "
            f"{_truncate(candidate.value, 30)}"
        )
    print("-" * 120)

    if annotate:
        annotated_path = screenshot_dir / f"inspect_{stamp}_annotated.png"
        annotate_candidates(
            capture.image,
            inspection.candidates,
            capture.origin,
            annotated_path,
        )
        print(f"Annotated: {annotated_path}")

    if not inspection.candidates:
        print("Warning: no UIA candidates found; the app may be custom-drawn.")
    return 0


def run_safety_test(
    config_path: str,
    seconds: float = 10.0,
    overlay: bool = True,
) -> int:
    """Block input for a fixed time so the safety shortcuts can be tested."""
    from .safety.controller import SafetyController
    from .safety.hotkeys import HotkeyError, hotkeys_from_config
    from .safety.overlay import NullOverlay, StatusOverlay

    try:
        config = load_config(config_path)
        hotkeys = hotkeys_from_config(config.safety)
    except (ConfigError, HotkeyError) as exc:
        print(f"ERROR: {exc}")
        return 1

    labels = {hotkey.action: hotkey.label for hotkey in hotkeys}
    controller = SafetyController(
        config.safety,
        overlay=StatusOverlay(labels) if overlay else NullOverlay(),
    )

    print("Safety test: external mouse and keyboard input will be blocked.")
    print(
        f"  {labels['stop']} stop | {labels['pause']} pause | "
        f"{labels['resume']} resume"
    )
    print("  Ctrl+Alt+Del is handled by Windows and is never blocked.")
    print(f"  Locking for up to {seconds:.0f}s; press {labels['stop']} to release.")
    print()

    try:
        controller.start()
    except Exception as exc:
        controller.close()
        print(f"ERROR: {exc}")
        return 1

    print("Input locked.", flush=True)

    try:
        controller.wait(seconds)
    except KeyboardInterrupt:
        controller.stop(reason="interrupted")

    reason = controller.reason or "timeout"
    controller.close()
    print(f"Input released ({reason}).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="excel-agent",
        description="AI-first Excel -> desktop data-entry agent.",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="check Python, dependencies, .env, config, and desktop support",
    )
    parser.add_argument(
        "--config",
        default="config/agent.example.json",
        help="path to the JSON configuration file",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="path to the .env file (defaults to <project>/.env)",
    )

    subparsers = parser.add_subparsers(dest="command")

    excel_parser = subparsers.add_parser("excel", help="Excel read/write (Segment 1)")
    excel_parser.add_argument("--config", default=argparse.SUPPRESS)
    excel_sub = excel_parser.add_subparsers(dest="excel_command")
    inspect_parser = excel_sub.add_parser("inspect", help="read the workbook and show rows")
    inspect_parser.add_argument("--config", default=argparse.SUPPRESS)
    inspect_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="maximum rows to display; 0 shows all (default: 20)",
    )

    window_parser = subparsers.add_parser(
        "inspect", help="capture the target window and list UIA controls (Segment 2)"
    )
    window_parser.add_argument("--config", default=argparse.SUPPRESS)
    window_parser.add_argument(
        "--max-elements",
        type=int,
        default=300,
        help="maximum UIA controls to list (default: 300)",
    )
    window_parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="skip the annotated screenshot",
    )

    safety_parser = subparsers.add_parser(
        "safety", help="input lock and hotkey test (Segment 3)"
    )
    safety_parser.add_argument("--config", default=argparse.SUPPRESS)
    safety_sub = safety_parser.add_subparsers(dest="safety_command")
    safety_test = safety_sub.add_parser(
        "test", help="block input; the stop hotkey releases it"
    )
    safety_test.add_argument("--config", default=argparse.SUPPRESS)
    safety_test.add_argument(
        "--seconds",
        type=float,
        default=10.0,
        help="how long to block input (default: 10)",
    )
    safety_test.add_argument(
        "--no-overlay",
        action="store_true",
        help="do not show the status overlay",
    )

    for command, segment in _FUTURE_COMMANDS.items():
        sub = subparsers.add_parser(command, help=f"planned for {segment}")
        sub.add_argument("--config", default=argparse.SUPPRESS)
        sub.add_argument("--env-file", default=argparse.SUPPRESS)
        sub.add_argument("--limit", type=int, default=None, help="row limit (future use)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"excel-desktop-agent {__version__}")
        return 0

    if args.check:
        return run_check(args.config, args.env_file)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "excel":
        if args.excel_command == "inspect":
            return run_excel_inspect(args.config, limit=args.limit)
        print("usage: python main.py excel inspect [--config PATH] [--limit N]")
        return 2

    if args.command == "inspect":
        return run_inspect(
            args.config,
            annotate=not args.no_annotate,
            max_elements=args.max_elements,
        )

    if args.command == "safety":
        if args.safety_command == "test":
            return run_safety_test(
                args.config,
                seconds=args.seconds,
                overlay=not args.no_overlay,
            )
        print("usage: python main.py safety test [--config PATH] [--seconds N]")
        return 2

    segment = _FUTURE_COMMANDS.get(args.command, "a later segment")
    print(f"'{args.command}' is not implemented yet; planned for {segment}.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
