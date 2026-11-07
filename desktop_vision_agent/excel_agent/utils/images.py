"""Small image helpers for desktop inspection (Segment 2)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..models.screen import UIACandidate


def annotate_candidates(
    image: Image.Image,
    candidates: tuple[UIACandidate, ...] | list[UIACandidate],
    origin: tuple[int, int],
    output_path: str | Path,
) -> Path:
    """Draw candidate boxes on a copy of the screenshot and save it."""
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    offset_x, offset_y = origin
    max_x, max_y = annotated.width - 1, annotated.height - 1

    for candidate in candidates:
        left = max(0, min(max_x, candidate.rect.left - offset_x))
        top = max(0, min(max_y, candidate.rect.top - offset_y))
        right = max(0, min(max_x, candidate.rect.right - offset_x))
        bottom = max(0, min(max_y, candidate.rect.bottom - offset_y))
        if right <= left or bottom <= top:
            continue

        draw.rectangle([left, top, right, bottom], outline="red", width=2)
        draw.text(
            (left + 2, max(0, top - 12)),
            candidate.candidate_id,
            fill="red",
        )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(path)
    return path
