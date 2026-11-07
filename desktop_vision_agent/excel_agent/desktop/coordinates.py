"""Convert Gemini's normalized boxes to screen pixels (Segment 4).

Gemini returns bounding boxes in a resolution-independent ``0-1000`` space
relative to the captured screenshot. This module converts them to image
pixels and to physical screen coordinates, and checks that a rectangle stays
inside the captured window.
"""

from __future__ import annotations

from .capture import Capture
from ..models.screen import Rect

NORMALIZED_MAX = 1000


class CoordinateError(ValueError):
    """A normalized box cannot be converted to a valid rectangle."""


def _check_normalized(value: float, name: str) -> None:
    if not 0 <= value <= NORMALIZED_MAX:
        raise CoordinateError(
            f"{name} must be between 0 and {NORMALIZED_MAX}, got {value}"
        )


def _check_box(x1: float, y1: float, x2: float, y2: float) -> None:
    _check_normalized(x1, "x1")
    _check_normalized(y1, "y1")
    _check_normalized(x2, "x2")
    _check_normalized(y2, "y2")
    if x1 >= x2 or y1 >= y2:
        raise CoordinateError(
            f"normalized box must have x1 < x2 and y1 < y2, got "
            f"({x1}, {y1}, {x2}, {y2})"
        )


def normalized_to_image(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    image_width: int,
    image_height: int,
) -> Rect:
    """Convert a ``0-1000`` box to a pixel rectangle inside the image."""
    _check_box(x1, y1, x2, y2)
    if image_width <= 0 or image_height <= 0:
        raise CoordinateError(
            f"image size must be positive, got {image_width}x{image_height}"
        )

    left = round(x1 / NORMALIZED_MAX * image_width)
    top = round(y1 / NORMALIZED_MAX * image_height)
    right = round(x2 / NORMALIZED_MAX * image_width)
    bottom = round(y2 / NORMALIZED_MAX * image_height)

    left = min(max(0, left), image_width - 1)
    top = min(max(0, top), image_height - 1)
    right = min(image_width, max(left + 1, right))
    bottom = min(image_height, max(top + 1, bottom))
    return Rect(left, top, right, bottom)


def normalized_to_screen(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    capture: Capture,
) -> Rect:
    """Convert a ``0-1000`` box to physical screen coordinates.

    The box is scaled against the captured window, not the whole desktop, so
    ``(0, 0)`` is the window's top-left corner at capture time.
    """
    _check_box(x1, y1, x2, y2)

    width = capture.image.width * capture.scale_x
    height = capture.image.height * capture.scale_y
    origin_x, origin_y = capture.origin

    left = origin_x + round(x1 / NORMALIZED_MAX * width)
    top = origin_y + round(y1 / NORMALIZED_MAX * height)
    right = origin_x + round(x2 / NORMALIZED_MAX * width)
    bottom = origin_y + round(y2 / NORMALIZED_MAX * height)

    right = max(left + 1, right)
    bottom = max(top + 1, bottom)
    return Rect(left, top, right, bottom)


def rect_within(rect: Rect, bounds: Rect, tolerance: int = 1) -> bool:
    """True when *rect* lies inside *bounds*, allowing a pixel of rounding."""
    return (
        rect.left >= bounds.left - tolerance
        and rect.top >= bounds.top - tolerance
        and rect.right <= bounds.right + tolerance
        and rect.bottom <= bounds.bottom + tolerance
    )
