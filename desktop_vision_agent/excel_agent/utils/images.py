"""Small image helpers for desktop inspection and grounding (Segments 2-4)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw

from ..models.screen import Rect, UIACandidate


def prepare_image(image: Image.Image, max_dimension: int) -> Image.Image:
    """Return *image* scaled down so its longest side fits *max_dimension*."""
    if max_dimension <= 0:
        raise ValueError(f"max_dimension must be positive, got {max_dimension}")

    width, height = image.size
    longest = max(width, height)
    if longest <= max_dimension:
        return image

    ratio = max_dimension / longest
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return image.resize(size, Image.LANCZOS)


def _annotate(
    image: Image.Image,
    items: Iterable[tuple[str, Rect, str]],
    origin: tuple[int, int],
) -> Image.Image:
    """Draw labelled boxes (in screen coordinates) on a copy of *image*."""
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    offset_x, offset_y = origin
    max_x, max_y = annotated.width - 1, annotated.height - 1

    for label, rect, color in items:
        left = max(0, min(max_x, rect.left - offset_x))
        top = max(0, min(max_y, rect.top - offset_y))
        right = max(0, min(max_x, rect.right - offset_x))
        bottom = max(0, min(max_y, rect.bottom - offset_y))
        if right <= left or bottom <= top:
            continue

        draw.rectangle([left, top, right, bottom], outline=color, width=2)
        draw.text((left + 2, max(0, top - 12)), label, fill=color)
    return annotated


def annotate_candidates(
    image: Image.Image,
    candidates: tuple[UIACandidate, ...] | list[UIACandidate],
    origin: tuple[int, int],
    output_path: str | Path,
) -> Path:
    """Draw candidate boxes on a copy of the screenshot and save it."""
    items = [
        (candidate.candidate_id, candidate.rect, "red")
        for candidate in candidates
    ]
    annotated = _annotate(image, items, origin)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(path)
    return path


def annotate_boxes(
    image: Image.Image,
    boxes: Iterable[tuple[str, Rect, str]],
    origin: tuple[int, int],
    output_path: str | Path,
) -> Path:
    """Draw labelled ``(label, rect, color)`` boxes and save the result."""
    annotated = _annotate(image, boxes, origin)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(path)
    return path
