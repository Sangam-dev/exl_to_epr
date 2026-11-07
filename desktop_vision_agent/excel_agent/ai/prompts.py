"""Versioned prompt loading and rendering (Segment 4).

Prompts live in ``config/prompts/<name>.md`` so they can be edited without
touching code. ``{{TOKEN}}`` placeholders are replaced literally; JSON braces
in the prompt are left alone.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..config import project_root
from ..models.screen import UIACandidate

PROMPT_DIR = project_root() / "config" / "prompts"

_TOKEN_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


class PromptError(RuntimeError):
    """A prompt file is missing or a placeholder was not supplied."""


def load_prompt(name: str) -> str:
    """Read ``config/prompts/<name>.md``."""
    path = PROMPT_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"cannot read prompt {path}: {exc}") from exc


def render_prompt(template: str, values: dict[str, str]) -> str:
    """Replace ``{{TOKEN}}`` placeholders, rejecting missing values."""
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return match.group(0)
        return values[key]

    rendered = _TOKEN_RE.sub(_replace, template)
    if missing:
        raise PromptError(
            "missing prompt value(s): " + ", ".join(sorted(set(missing)))
        )
    return rendered


def format_candidates(candidates: Iterable[UIACandidate]) -> str:
    """Render UIA candidates as one compact line each."""
    lines: list[str] = []
    for candidate in candidates:
        lines.append(
            f"- {candidate.candidate_id} | type={candidate.control_type} | "
            f"name={candidate.name or '-'} | "
            f"automation_id={candidate.automation_id or '-'} | "
            f"value={candidate.value[:60] or '-'} | "
            f"rect={candidate.rect.bbox}"
        )
    return "\n".join(lines) if lines else "(no UIA candidates)"


def build_grounding_prompt(
    headers: Iterable[str],
    candidates: Iterable[UIACandidate],
    image_width: int,
    image_height: int,
) -> str:
    """Render the grounding prompt for one window."""
    candidate_list = list(candidates)
    template = load_prompt("grounding")
    return render_prompt(
        template,
        {
            "HEADERS": "\n".join(f"- {header}" for header in headers),
            "CANDIDATES": format_candidates(candidate_list),
            "CANDIDATE_COUNT": str(len(candidate_list)),
            "IMAGE_WIDTH": str(image_width),
            "IMAGE_HEIGHT": str(image_height),
        },
    )
