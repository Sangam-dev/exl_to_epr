"""Capture only the target window (Segment 2)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass

import win32gui
import win32ui
from PIL import Image, ImageGrab

from ..models.screen import WindowInfo
from .window import get_window

_user32 = ctypes.windll.user32
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL

_PW_RENDERFULLCONTENT = 0x00000002


class CaptureError(RuntimeError):
    """The target window could not be captured."""


@dataclass(frozen=True)
class Capture:
    """A screenshot of one window plus the image -> screen transform."""

    image: Image.Image
    origin: tuple[int, int]
    scale_x: float
    scale_y: float
    window: WindowInfo

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        """Convert an image pixel to a screen coordinate."""
        return (
            int(self.origin[0] + x * self.scale_x),
            int(self.origin[1] + y * self.scale_y),
        )


def _print_window(hwnd: int, width: int, height: int) -> Image.Image | None:
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None

    source_dc = None
    memory_dc = None
    bitmap = None
    try:
        source_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)

        if not _user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT):
            return None

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bits,
            "raw",
            "BGRX",
            0,
            1,
        )
    except Exception:
        return None
    finally:
        if bitmap is not None:
            win32gui.DeleteObject(bitmap.GetHandle())
        if memory_dc is not None:
            memory_dc.DeleteDC()
        if source_dc is not None:
            source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


def _looks_blank(image: Image.Image) -> bool:
    return image.convert("L").getextrema() == (0, 0)


def _has_black_padding(image: Image.Image) -> bool:
    """True when PrintWindow drew the window smaller than the bitmap.

    Windows renders DPI-virtualized windows at their virtual size inside a
    physical-size bitmap, leaving black padding on the right and bottom. Such
    an image is not aligned with UIA rectangles, so it must not be used.
    """
    mask = image.convert("L").point(lambda value: 255 if value > 8 else 0)
    box = mask.getbbox()
    if box is None:
        return True
    return box[2] < image.width * 0.98 or box[3] < image.height * 0.98


def capture_window(window: WindowInfo) -> Capture:
    """Capture the target window, falling back to a screen crop if needed."""
    if win32gui.IsIconic(window.handle):
        raise CaptureError("target window is minimized; restore it before capture")

    window = get_window(window.handle)
    width, height = window.rect.width, window.rect.height
    if width <= 0 or height <= 0:
        raise CaptureError(f"target window has no drawable area: {window.rect.bbox}")

    image = _print_window(window.handle, width, height)
    if (
        image is None
        or image.size != (width, height)
        or _looks_blank(image)
        or _has_black_padding(image)
    ):
        try:
            image = ImageGrab.grab(bbox=window.rect.bbox, all_screens=True)
        except Exception as exc:
            raise CaptureError(f"could not capture {window.title!r}: {exc}") from exc

    if image.width <= 0 or image.height <= 0:
        raise CaptureError("captured image is empty")

    return Capture(
        image=image,
        origin=(window.rect.left, window.rect.top),
        scale_x=window.rect.width / image.width,
        scale_y=window.rect.height / image.height,
        window=window,
    )
