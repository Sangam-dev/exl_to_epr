"""Find a freshly saved row that landed below the fold.

An app that appends the new record to a long list only draws the rows near the
viewport, so the row a Save just created is missing from the UI Automation tree
until the list is scrolled. This module searches for it the one way that works
everywhere: turn the wheel down and read again.

It is written for the apps where the list scrolls while the Add button and the
entry form stay where they are - so the search only ever moves *down* from
wherever the previous row left the view, and nothing is scrolled back.

If the Add button disappears while the screen moves, the page as a whole is
scrolling: that is a different app shape, and the search says so instead of
carrying on and clicking a button that is no longer there.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from ..desktop.scroll import page_down_window, scroll_window
from ..desktop.uia import UIAError, UIAInspection, inspect_window
from ..models.screen import WindowInfo
from .confirmation import CONFIRMED, REJECTED, UNKNOWN, ConfirmationResult
from .row_evidence import check_row_appeared, form_still_shows, screen_text

logger = logging.getLogger(__name__)

# One screen of a browser page is about this many wheel notches.
SCREEN_NOTCHES = 5
# Chrome draws the scrolled rows a moment after the wheel event.
SETTLE_SECONDS = 0.4
# Safety net: an appended row sits a screen below the previous one, never six.
MAX_SCREENS = 6


class RowSearchUnsupported(RuntimeError):
    """The whole page scrolls, so the downward row search cannot help."""


@dataclass(frozen=True)
class RowSearch:
    """What the downward search found, and how far it had to go."""

    result: ConfirmationResult
    screens: int = 0
    moved: bool = False
    fatal: str = ""


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def _present(text: str, labels: Sequence[str]) -> list[str]:
    return [label for label in labels if _normalise(label) and _normalise(label) in text]


def _still_open(inspection: UIAInspection | None, values: dict[str, str], footprint: Sequence[str]) -> bool:
    """True when the entry form is still on screen, so no row was saved."""
    for column in footprint:
        if form_still_shows(inspection, values.get(column, "")):
            return True
    return False


def _searched(result: ConfirmationResult, screens: int) -> ConfirmationResult:
    """Reword a miss so the status says the list was actually searched."""
    if result.outcome != UNKNOWN or not screens:
        return result
    return replace(
        result,
        reason=(
            f"scrolled {screens} screen(s) to the end of the list; {result.reason}"
        ),
    )


def find_appended_row(
    after_save: UIAInspection | None,
    *,
    window: WindowInfo,
    before: UIAInspection | None,
    values: dict[str, str],
    footprint: Sequence[str],
    error_patterns: Sequence[str] = (),
    anchor_labels: Sequence[str] = (),
    max_screens: int = MAX_SCREENS,
    settle_seconds: float = SETTLE_SECONDS,
    wheel_notches: int = SCREEN_NOTCHES,
    page_down: Callable[[WindowInfo], bool] | None = page_down_window,
    note: Callable[[str], None] | None = None,
    read: Callable[[WindowInfo], UIAInspection | None] | None = None,
    scroll: Callable[[WindowInfo, int], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RowSearch:
    """Confirm a save from the screen, scrolling down only when it is not there.

    The screen after the Save click is checked first, so a row already in view
    costs nothing. From then on each step scrolls one screen down, settles, and
    re-reads; the same "value appears more often than before" test is applied,
    so scrolling can only ever add evidence.

    Never scrolls when the form is still open (the save was rejected, and the
    row being searched for does not exist) and never scrolls upwards.
    """
    if after_save is None:
        return RowSearch(
            result=ConfirmationResult(
                outcome=UNKNOWN,
                reason="could not read the window after Save",
                source="window text",
            )
        )

    read = read or _read
    scroll = scroll or scroll_window

    def decide(inspection: UIAInspection) -> ConfirmationResult:
        return check_row_appeared(
            before, inspection, values, footprint, error_patterns
        )

    inspection = after_save
    result = decide(inspection)
    if result.outcome in (CONFIRMED, REJECTED) or _still_open(
        inspection, values, footprint
    ):
        return RowSearch(result=result)

    previous = screen_text(inspection)
    anchors = _present(previous, anchor_labels)
    if note:
        note("  not on this screen; scrolling down to look for it")
    screens = 0
    strikes = 0
    moved = False
    used_keys = False

    def advance() -> UIAInspection | None:
        """One screen further down, or ``None`` when the app will not move."""
        nonlocal used_keys
        if not scroll(window, wheel_notches):
            return None
        if settle_seconds > 0:
            sleep(settle_seconds)
        fresh = read(window)
        if fresh is None or screen_text(fresh) != previous:
            return fresh
        # The wheel changed nothing: it may have landed on our own status
        # overlay, or the page may ignore a wheel no pointer can reach. Page
        # Down moves the focused page instead, so try it once before calling
        # this the end of the list.
        if used_keys or page_down is None or not page_down(window):
            return fresh
        used_keys = True
        if settle_seconds > 0:
            sleep(settle_seconds)
        return read(window)

    while screens < max_screens:
        fresh = advance()
        if fresh is None:
            break
        screens += 1

        result = decide(fresh)
        text = screen_text(fresh)
        if text != previous:
            moved = True
            strikes = 0
            if note:
                note(f"  scroll {screens}: one screen down")
        else:
            strikes += 1
            if note:
                note(f"  scroll {screens}: the screen did not change")

        if result.outcome in (CONFIRMED, REJECTED):
            inspection = fresh
            break
        if anchors and not _present(text, anchors):
            logger.warning(
                "row search unsupported: the anchor %r scrolled away", anchors[0]
            )
            return RowSearch(
                result=result,
                screens=screens,
                moved=moved,
                fatal=(
                    f"the Add button {anchors[0]!r} scrolled out of view while "
                    "looking for the new row, so this app scrolls the whole "
                    "page; that app type is not supported yet"
                ),
            )
        if _still_open(fresh, values, footprint):
            inspection = fresh
            break
        previous = text
        inspection = fresh
        if strikes >= 2:
            break

    if result.outcome == CONFIRMED and screens:
        result = replace(
            result, reason=f"{result.reason} ({screens} screen(s) down)"
        )
    if result.outcome == UNKNOWN:
        # Say which of the three things happened: the list was read to its end,
        # it never moved, or it could not be moved at all. A miss that claims
        # the list was searched when nothing moved sends the next run looking
        # for the wrong problem.
        if screens and not moved:
            result = replace(
                result,
                reason=(
                    f"the screen did not move when it was scrolled "
                    f"({screens} attempt(s)), so the rest of the list could not "
                    f"be read; {result.reason}"
                ),
            )
        elif not screens:
            result = replace(
                result,
                reason=(
                    "the page could not be scrolled, so only the screen after "
                    f"Save was checked; {result.reason}"
                ),
            )
        else:
            result = _searched(result, screens)
    logger.info(
        "row search: %s after %s screen(s) (moved=%s) - %s",
        result.outcome,
        screens,
        moved,
        result.reason,
    )
    return RowSearch(result=result, screens=screens, moved=moved)


def _read(window: WindowInfo) -> UIAInspection | None:
    """Read the window wide enough to include table rows, or ``None``."""
    try:
        return inspect_window(window, include_data_items=True)
    except UIAError:
        return None
