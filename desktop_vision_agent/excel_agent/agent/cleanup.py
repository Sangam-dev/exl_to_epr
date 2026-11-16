"""Select and delete runtime data left behind by previous runs.

``clear-data`` is deliberately split in two steps: :func:`build_plan` finds
what a set of categories would remove without touching anything, and
:func:`execute_plan` performs the deletions. The CLI prints the plan and asks
for confirmation in between, so a mistyped flag can never wipe a workbook
before the operator has seen the counts.

Only files are deleted; :func:`prune_empty_dirs` then removes directories that
became empty, while the ``.gitkeep`` in every ``data/`` folder keeps the tree
itself intact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..config import project_root

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})
SCREENSHOT_DIRS = ("screenshots", "setup", "grounding", "steps", "annotated")
KEEP_NAMES = frozenset({".gitkeep"})

# Categories that ``--all`` covers. ``input`` and ``mapping`` are excluded on
# purpose: the source workbook cannot be recreated, and an approved mapping
# costs a fresh (paid) ``setup`` run.
ALL_CATEGORIES = (
    "screenshots",
    "ai_logs",
    "logs",
    "output",
    "state",
    "backup",
)

STATE_FILES = ("run_state.json", "pending_status.json", "output.json", "run.lock")
STATE_PATTERNS = ("run_state.stale-*.json", "backup_*")


@dataclass(frozen=True)
class CleanupTarget:
    """One user-selectable group of files."""

    key: str
    label: str
    description: str
    in_all: bool = True


TARGETS: dict[str, CleanupTarget] = {
    "screenshots": CleanupTarget(
        "screenshots",
        "Screenshots",
        "images under data/screenshots, setup, grounding, steps, annotated",
    ),
    "ai_logs": CleanupTarget("ai_logs", "AI logs", "data/ai_logs"),
    "logs": CleanupTarget("logs", "Logs", "data/logs (an open agent.log is emptied)"),
    "output": CleanupTarget(
        "output", "Working copies", "data/output plus the workspace pointer"
    ),
    "state": CleanupTarget(
        "state",
        "Run state",
        "run_state, pending_status, output pointer, run.lock, state backups",
    ),
    "mapping": CleanupTarget("mapping", "Mapping", "data/state/mapping.json", False),
    "input": CleanupTarget(
        "input", "Input workbooks", "data/input (your source data)", False
    ),
    "backup": CleanupTarget("backup", "Backups", "data/backup"),
    "cache": CleanupTarget(
        "cache", "Python cache", "__pycache__ and .pytest_cache folders", False
    ),
    "configs": CleanupTarget(
        "configs",
        "Generated configs",
        "config/agent.interactive*.json and config/agent.run*.json",
        False,
    ),
}


class CleanupError(ValueError):
    """The requested category set is not valid."""


@dataclass(frozen=True)
class PlannedFile:
    """One file the plan would delete."""

    path: Path
    size: int = 0
    # Log files can still be held open by this process; emptying them is the
    # only thing Windows allows.
    truncate_if_locked: bool = False


@dataclass(frozen=True)
class CategoryPlan:
    """Planned deletions for one category."""

    key: str
    label: str
    files: tuple[PlannedFile, ...] = ()

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True)
class CleanupPlan:
    """Everything a ``clear-data`` invocation would delete."""

    categories: tuple[CategoryPlan, ...] = ()

    @property
    def files(self) -> tuple[PlannedFile, ...]:
        return tuple(item for group in self.categories for item in group.files)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


@dataclass
class CleanupResult:
    """What :func:`execute_plan` actually did."""

    deleted: list[Path] = field(default_factory=list)
    truncated: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    freed_bytes: int = 0


def resolve_categories(keys: Sequence[str]) -> tuple[str, ...]:
    """Expand ``all`` and validate the requested category names."""
    resolved: list[str] = []
    for key in keys:
        name = key.strip().lower()
        if not name:
            continue
        if name == "all":
            candidates: Iterable[str] = ALL_CATEGORIES
        elif name in TARGETS:
            candidates = (name,)
        else:
            raise CleanupError(f"unknown cleanup category: {key}")
        for candidate in candidates:
            if candidate not in resolved:
                resolved.append(candidate)
    return tuple(resolved)


def is_inside(path: str | Path, root: str | Path) -> bool:
    """True when *path* resolves to something inside *root*."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _walk(
    directory: Path,
    *,
    suffixes: frozenset[str] | None = None,
    truncate_if_locked: bool = False,
) -> list[PlannedFile]:
    """Every deletable file below *directory*, keeping ``.gitkeep``."""
    if not directory.is_dir():
        return []
    found: list[PlannedFile] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in KEEP_NAMES:
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        found.append(
            PlannedFile(
                path=path,
                size=_size(path),
                truncate_if_locked=truncate_if_locked,
            )
        )
    return found


def _screenshot_files(root: Path) -> list[PlannedFile]:
    data = root / "data"
    found: list[PlannedFile] = []
    for name in SCREENSHOT_DIRS:
        found.extend(_walk(data / name, suffixes=IMAGE_SUFFIXES))
    return found


def _state_files(root: Path) -> list[PlannedFile]:
    state = root / "data" / "state"
    found: list[PlannedFile] = []
    for name in STATE_FILES:
        path = state / name
        if path.is_file():
            found.append(PlannedFile(path=path, size=_size(path)))
    for pattern in STATE_PATTERNS:
        for path in sorted(state.glob(pattern)):
            if path.is_file():
                found.append(PlannedFile(path=path, size=_size(path)))
            elif path.is_dir():
                found.extend(_walk(path))
    return found


def _output_files(root: Path) -> list[PlannedFile]:
    # The pointer file has to go with the workbooks: open_workspace() refuses
    # to continue when it records a working copy that no longer exists.
    found = _walk(root / "data" / "output")
    pointer = root / "data" / "state" / "output.json"
    if pointer.is_file():
        found.append(PlannedFile(path=pointer, size=_size(pointer)))
    return found


def _mapping_files(root: Path) -> list[PlannedFile]:
    path = root / "data" / "state" / "mapping.json"
    return [PlannedFile(path=path, size=_size(path))] if path.is_file() else []


def _cache_files(root: Path) -> list[PlannedFile]:
    found: list[PlannedFile] = []
    for pattern in ("**/__pycache__", ".pytest_cache", "**/.pytest_cache"):
        for directory in sorted(root.glob(pattern)):
            if directory.is_dir():
                found.extend(_walk(directory))
    return found


def _config_files(root: Path) -> list[PlannedFile]:
    config = root / "config"
    found: list[PlannedFile] = []
    for pattern in ("agent.interactive.json", "agent.interactive.steps.json", "agent.run*.json"):
        for path in sorted(config.glob(pattern)):
            if path.is_file():
                found.append(PlannedFile(path=path, size=_size(path)))
    return found


def _collect(key: str, root: Path) -> list[PlannedFile]:
    data = root / "data"
    if key == "screenshots":
        return _screenshot_files(root)
    if key == "ai_logs":
        return _walk(data / "ai_logs")
    if key == "logs":
        return _walk(data / "logs", truncate_if_locked=True)
    if key == "output":
        return _output_files(root)
    if key == "state":
        return _state_files(root)
    if key == "mapping":
        return _mapping_files(root)
    if key == "input":
        return _walk(data / "input")
    if key == "backup":
        return _walk(data / "backup")
    if key == "cache":
        return _cache_files(root)
    if key == "configs":
        return _config_files(root)
    raise CleanupError(f"unknown cleanup category: {key}")


def build_plan(
    keys: Sequence[str], root: str | Path | None = None
) -> CleanupPlan:
    """Work out what *keys* would delete under *root*, without deleting it."""
    base = Path(root) if root is not None else project_root()
    categories: list[CategoryPlan] = []
    seen: set[Path] = set()
    for key in resolve_categories(keys):
        files: list[PlannedFile] = []
        for item in _collect(key, base):
            resolved = item.path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(item)
        categories.append(
            CategoryPlan(key=key, label=TARGETS[key].label, files=tuple(files))
        )
    return CleanupPlan(categories=tuple(categories))


def pending_status_entries(path: str | Path | None = None) -> int:
    """How many queued row statuses the sidecar holds."""
    sidecar = (
        Path(path)
        if path is not None
        else project_root() / "data" / "state" / "pending_status.json"
    )
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    entries = data.get("entries") if isinstance(data, dict) else None
    return len(entries) if isinstance(entries, list) else 0


def active_run_pid(path: str | Path | None = None) -> int | None:
    """The pid of a live run holding the lock, or ``None``."""
    lock = (
        Path(path) if path is not None else project_root() / "data" / "state" / "run.lock"
    )
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = data.get("pid") if isinstance(data, dict) else None
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    from .run_lock import _pid_alive

    return pid if _pid_alive(pid) else None


def execute_plan(plan: CleanupPlan, root: str | Path | None = None) -> CleanupResult:
    """Delete every planned file that is still inside the project folder."""
    base = Path(root) if root is not None else project_root()
    result = CleanupResult()
    touched: set[Path] = set()

    for item in plan.files:
        path = Path(item.path)
        if not is_inside(path, base):
            result.skipped.append((path, "outside the project folder"))
            continue
        if not path.exists():
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            if item.truncate_if_locked:
                try:
                    with path.open("w", encoding="utf-8"):
                        pass
                except OSError:
                    pass
                else:
                    result.truncated.append(path)
                    continue
            result.skipped.append((path, str(exc) or type(exc).__name__))
            continue

        result.deleted.append(path)
        result.freed_bytes += item.size
        touched.add(path.parent)

    prune_empty_dirs(touched, base)
    return result


def prune_empty_dirs(directories: Iterable[Path], root: str | Path) -> None:
    """Remove emptied sub-folders, keeping ``data/`` and its categories."""
    base = Path(root).resolve()
    data_root = base / "data"
    for start in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        current = Path(start)
        while current != base and current != data_root:
            if current.parent == data_root:
                break  # data/<category> survives even when emptied
            try:
                if any(current.iterdir()):
                    break
                current.rmdir()
            except OSError:
                break
            current = current.parent


def human_size(size: int) -> str:
    """Format a byte count for the preview table."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def display_path(path: str | Path, root: str | Path | None = None) -> str:
    """Show a path relative to the project root when possible."""
    base = Path(root) if root is not None else project_root()
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except (ValueError, OSError):
        return str(path)


def format_plan(plan: CleanupPlan) -> str:
    """Render the preview table shown before the confirmation prompt."""
    if not plan.categories:
        return "Nothing selected."
    width = max(len(group.label) for group in plan.categories)
    lines = []
    for group in plan.categories:
        label = group.label.ljust(width)
        lines.append(
            f"{label}  {group.count:>5} file(s)  {human_size(group.total_bytes):>9}"
            f"  {TARGETS[group.key].description}"
        )
    lines.append("-" * (width + 40))
    lines.append(
        f"{'Total'.ljust(width)}  {plan.total_files:>5} file(s)  "
        f"{human_size(plan.total_bytes):>9}"
    )
    return "\n".join(lines)


def record_summary(plan: CleanupPlan, result: CleanupResult) -> str:
    """One-line audit record for the log file."""
    return (
        f"clear-data {','.join(group.key for group in plan.categories)}: "
        f"deleted {len(result.deleted)}, truncated {len(result.truncated)}, "
        f"skipped {len(result.skipped)}, freed {result.freed_bytes} bytes"
    )


__all__ = [
    "ALL_CATEGORIES",
    "CleanupError",
    "CleanupPlan",
    "CleanupResult",
    "CleanupTarget",
    "CategoryPlan",
    "PlannedFile",
    "TARGETS",
    "active_run_pid",
    "build_plan",
    "display_path",
    "execute_plan",
    "format_plan",
    "human_size",
    "is_inside",
    "pending_status_entries",
    "prune_empty_dirs",
    "record_summary",
    "resolve_categories",
]
