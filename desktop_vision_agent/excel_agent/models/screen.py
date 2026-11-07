"""Screen, window, and UIA candidate models (Segment 2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """A rectangle in physical screen pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.left
            or self.left >= other.right
            or self.bottom <= other.top
            or self.top >= other.bottom
        )


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window snapshot."""

    handle: int
    title: str
    process_name: str
    class_name: str
    rect: Rect
    client_rect: Rect
    dpi: int

    @property
    def dpi_scale(self) -> float:
        return self.dpi / 96.0


@dataclass(frozen=True)
class UIACandidate:
    """One UI Automation control found inside the target window."""

    candidate_id: str
    name: str
    control_type: str
    automation_id: str
    class_name: str
    rect: Rect
    value: str
    enabled: bool
