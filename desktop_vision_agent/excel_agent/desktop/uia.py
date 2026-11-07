"""UI Automation inspection of the target window (Segment 2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pywinauto import Desktop

from ..models.screen import Rect, UIACandidate, WindowInfo

# Controls that are useful for grounding: fields, buttons, and labels.
_INTERESTING_TYPES = {
    "Button",
    "CheckBox",
    "ComboBox",
    "Document",
    "Edit",
    "Header",
    "HeaderItem",
    "ListItem",
    "MenuItem",
    "RadioButton",
    "Spinner",
    "TabItem",
    "Text",
}


class UIAError(RuntimeError):
    """UI Automation could not inspect or act on the target window."""


@dataclass
class UIAInspection:
    """The candidates found in one window and the live controls behind them."""

    window: WindowInfo
    candidates: tuple[UIACandidate, ...]
    _controls: dict[str, Any] = field(repr=False)

    def candidate(self, candidate_id: str) -> UIACandidate:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise UIAError(f"unknown UIA candidate {candidate_id!r}")

    def _control(self, candidate_id: str) -> Any:
        try:
            return self._controls[candidate_id]
        except KeyError as exc:
            raise UIAError(f"unknown UIA candidate {candidate_id!r}") from exc

    def focus(self, candidate_id: str) -> None:
        """Move keyboard focus to a control."""
        try:
            self._control(candidate_id).set_focus()
        except UIAError:
            raise
        except Exception as exc:
            raise UIAError(f"could not focus {candidate_id}: {exc}") from exc

    def read_value(self, candidate_id: str) -> str:
        """Read the control's current value, if it exposes one."""
        return _read_value(self._control(candidate_id))

    def invoke(self, candidate_id: str) -> None:
        """Invoke a control through its native UIA pattern (no mouse)."""
        try:
            self._control(candidate_id).invoke()
        except UIAError:
            raise
        except Exception as exc:
            raise UIAError(
                f"could not invoke {candidate_id}; it may not support Invoke: {exc}"
            ) from exc


def _read_value(control: Any) -> str:
    try:
        iface = control.iface_value
        if iface is not None:
            return str(iface.CurrentValue or "")
    except Exception:
        pass
    try:
        return str(control.window_text() or "")
    except Exception:
        return ""


def _rect(value: Any) -> Rect | None:
    try:
        left, top, right, bottom = (
            int(value.left),
            int(value.top),
            int(value.right),
            int(value.bottom),
        )
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return Rect(left, top, right, bottom)


def inspect_window(window: WindowInfo, max_elements: int = 300) -> UIAInspection:
    """Inspect only the target window's UIA subtree."""
    try:
        spec = Desktop(backend="uia").window(handle=window.handle)
        spec.wait("exists", timeout=3.0)
        controls = spec.descendants()
    except Exception as exc:
        raise UIAError(f"could not inspect window 0x{window.handle:X}: {exc}") from exc

    candidates: list[UIACandidate] = []
    live_controls: dict[str, Any] = {}

    for control in controls:
        if len(candidates) >= max_elements:
            break
        try:
            info = control.element_info
            if not bool(getattr(info, "visible", True)):
                continue
            control_type = str(getattr(info, "control_type", "") or "")
            if control_type not in _INTERESTING_TYPES:
                continue
            bounds = _rect(getattr(info, "rectangle", None))
            if bounds is None or not bounds.intersects(window.rect):
                continue

            name = str(getattr(info, "name", "") or "").strip()
            automation_id = str(getattr(info, "automation_id", "") or "").strip()
            value = _read_value(control)
            if not (name or automation_id or value):
                continue

            candidate_id = f"W{len(candidates) + 1}"
            candidates.append(
                UIACandidate(
                    candidate_id=candidate_id,
                    name=name,
                    control_type=control_type,
                    automation_id=automation_id,
                    class_name=str(getattr(info, "class_name", "") or ""),
                    rect=bounds,
                    value=value,
                    enabled=bool(getattr(info, "enabled", True)),
                )
            )
            live_controls[candidate_id] = control
        except Exception:
            continue

    return UIAInspection(
        window=window,
        candidates=tuple(candidates),
        _controls=live_controls,
    )
