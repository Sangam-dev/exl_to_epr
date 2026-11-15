"""Local proof that a saved row now shows up on the screen.

Plenty of apps never display a success message: the entry form simply closes
and the record appears in the list. For those, reading text has nothing to match
and every save would come back ``UNKNOWN``. What they do offer is the record
itself, and UI Automation already exposes it - so a save can be confirmed
without a model call, a request, or a screenshot leaving the machine.

The ``footprint`` is the set of columns whose values must show up. It is
decided once, from the first saved row: see :func:`decide_footprint`.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ..desktop.uia import UIAInspection
from ..models.screen import UIACandidate
from .calibration import EDITABLE_CONTROL_TYPES
from .confirmation import CONFIRMED, REJECTED, UNKNOWN, ConfirmationResult


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _parts(inspection: UIAInspection | None) -> list[tuple[str, str]]:
    """``(raw, normalised)`` text of every control that is not a form field."""
    if inspection is None:
        return []
    found: list[tuple[str, str]] = []
    for candidate in inspection.candidates:
        if candidate.control_type in EDITABLE_CONTROL_TYPES:
            continue
        for text in (candidate.name, candidate.value):
            cleaned = _normalise(text)
            if cleaned:
                found.append((text, cleaned))
    return found


def screen_text(inspection: UIAInspection | None) -> str:
    """The window's non-field text, one entry per control, normalised."""
    return " | ".join(normalised for _raw, normalised in _parts(inspection))


def form_still_shows(inspection: UIAInspection | None, value: str) -> bool:
    """True when an editable field still holds *value* (the form never closed)."""
    if inspection is None:
        return False
    wanted = _normalise(value)
    if not wanted:
        return False
    for candidate in inspection.candidates:
        if candidate.control_type not in EDITABLE_CONTROL_TYPES:
            continue
        if _normalise(candidate.value) == wanted or _normalise(candidate.name) == wanted:
            return True
    return False


def _count(text: str, value: str) -> int:
    return text.count(value) if value else 0


def footprint_columns(config: Any) -> list[str]:
    """The columns whose values prove a save, defaulting to the ID column."""
    configured = list(config.confirmation.footprint)
    if configured:
        return configured
    return [config.excel.id_column]


def check_row_appeared(
    before: UIAInspection | None,
    after: UIAInspection | None,
    values: dict[str, str],
    footprint: Sequence[str],
    error_patterns: Sequence[str] = (),
) -> ConfirmationResult:
    """Decide a save from the live screen alone, after the Save click.

    ``CONFIRMED`` needs the footprint value to be on the screen *more often*
    than before the click, so a row that was already listed cannot pass for a
    new one, and no form field may still hold it, so a rejected entry that is
    still open cannot pass either.
    """
    raw_after = " | ".join(raw for raw, _normalised in _parts(after))
    normalised_after = screen_text(after)
    evidence = (f"screen text: {len(normalised_after)} chars",)

    for pattern in error_patterns:
        try:
            match = re.search(pattern, raw_after)
        except re.error:
            continue
        if match is not None:
            return ConfirmationResult(
                outcome=REJECTED,
                reason=f"error text matched {pattern!r} ({match.group(0).strip()!r})",
                evidence=evidence,
                source="window text",
            )

    normalised_before = screen_text(before)
    looked_at: list[str] = []
    for column in footprint:
        value = _normalise(values.get(column, ""))
        if not value:
            continue
        looked_at.append(column)
        if form_still_shows(after, value):
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=(
                    f"the form still shows {values.get(column)!r}, so the save "
                    "may not have gone through"
                ),
                evidence=evidence,
                source="window text",
            )
        if _count(normalised_after, value) > _count(normalised_before, value):
            return ConfirmationResult(
                outcome=CONFIRMED,
                reason=(
                    f"the row appeared: {values.get(column)!r} from "
                    f"{column!r} is now on the screen"
                ),
                evidence=evidence,
                source="window text",
            )

    if not looked_at:
        reason = "no footprint value was available for this row"
    else:
        reason = (
            "none of the saved values appeared on the screen ("
            + ", ".join(repr(column) for column in looked_at)
            + ")"
        )
    return ConfirmationResult(
        outcome=UNKNOWN, reason=reason, evidence=evidence, source="window text"
    )


def decide_footprint(
    before: UIAInspection | None,
    after: UIAInspection | None,
    values: dict[str, str],
    columns: Sequence[str],
    preferred: str = "",
) -> list[str]:
    """Pick the columns whose values appeared on screen after the first save.

    Returns an empty list when nothing could be observed, so the caller can ask
    the model or fall back to the ID column.
    """
    if after is None:
        return []
    normalised_before = screen_text(before)
    normalised_after = screen_text(after)
    appeared: list[str] = []
    for column in columns:
        value = _normalise(values.get(column, ""))
        if value and _count(normalised_after, value) > _count(normalised_before, value):
            appeared.append(column)
    if preferred and preferred in appeared:
        return [preferred]
    if appeared:
        return [appeared[0]]
    return []
