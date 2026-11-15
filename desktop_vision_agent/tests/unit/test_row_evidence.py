"""Unit tests for confirming a Save from the window text, with no model call."""

from __future__ import annotations

from excel_agent.agent.confirmation import CONFIRMED, REJECTED, UNKNOWN
from excel_agent.agent.row_evidence import (
    check_row_appeared,
    decide_footprint,
    form_still_shows,
    screen_text,
)
from excel_agent.desktop.uia import UIAInspection
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo


def _candidate(
    name: str, *, control_type: str = "Text", value: str = ""
) -> UIACandidate:
    return UIACandidate(
        candidate_id=f"W{abs(hash((name, control_type, value))) % 10000}",
        name=name,
        control_type=control_type,
        automation_id="",
        class_name="",
        rect=Rect(0, 0, 10, 10),
        value=value,
        enabled=True,
    )


def _inspection(*candidates: UIACandidate) -> UIAInspection:
    window = WindowInfo(
        handle=1,
        title="Clients",
        process_name="brave.exe",
        class_name="Chrome_WidgetWin_1",
        rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100),
        dpi=96,
    )
    return UIAInspection(window=window, candidates=candidates, _controls={})


FORM = (
    _candidate("Client ID", control_type="Edit", value=""),
    _candidate("Client / Company Name", control_type="Edit", value=""),
)

TABLE_ROW_ONE = (_candidate("CL-1001"), _candidate("Himalayan Traders"))
TABLE_TWO_ROWS = TABLE_ROW_ONE + (_candidate("CL-1002"), _candidate("Everest"))


def test_only_non_field_text_counts_as_evidence() -> None:
    inspection = _inspection(
        _candidate("Client ID", control_type="Edit", value="CL-1002"),
        _candidate("Clients registered"),
    )

    text = screen_text(inspection)

    assert "clients registered" in text
    assert "cl-1002" not in text


def test_a_field_that_still_holds_the_value_means_the_form_is_open() -> None:
    inspection = _inspection(
        _candidate("Client ID", control_type="Edit", value="CL-1002")
    )

    assert form_still_shows(inspection, "CL-1002") is True
    assert form_still_shows(inspection, "CL-9999") is False


def test_the_new_row_confirms_the_save() -> None:
    before = _inspection(*(FORM + TABLE_ROW_ONE))
    after = _inspection(*TABLE_TWO_ROWS)

    result = check_row_appeared(before, after, {"Client ID": "CL-1002"}, ["Client ID"])

    assert result.outcome == CONFIRMED
    assert "CL-1002" in result.reason


def test_a_repeated_value_still_confirms_when_it_appears_once_more() -> None:
    before = _inspection(_candidate("Everest Supplies"))
    after = _inspection(_candidate("Everest Supplies"), _candidate("Everest Supplies"))

    result = check_row_appeared(
        before, after, {"Company": "Everest Supplies"}, ["Company"]
    )

    assert result.outcome == CONFIRMED


def test_a_value_that_was_already_listed_does_not_confirm() -> None:
    before = _inspection(_candidate("CL-1002"))
    after = _inspection(_candidate("CL-1002"))

    result = check_row_appeared(before, after, {"Client ID": "CL-1002"}, ["Client ID"])

    assert result.outcome == UNKNOWN
    assert "none of the saved values appeared" in result.reason


def test_a_form_that_stayed_open_does_not_confirm() -> None:
    before = _inspection(*(FORM + TABLE_ROW_ONE))
    after = _inspection(
        _candidate("Client ID", control_type="Edit", value="CL-1002"),
        _candidate("CL-1002"),
    )

    result = check_row_appeared(before, after, {"Client ID": "CL-1002"}, ["Client ID"])

    assert result.outcome == UNKNOWN
    assert "still shows" in result.reason


def test_matching_error_text_rejects_the_save() -> None:
    after = _inspection(_candidate("This client ID is already used."))

    result = check_row_appeared(
        None,
        after,
        {"Client ID": "CL-1002"},
        ["Client ID"],
        ["(?i)already used"],
    )

    assert result.outcome == REJECTED
    assert "already used" in result.reason


def test_the_footprint_prefers_the_id_column() -> None:
    before = _inspection(*FORM)
    after = _inspection(*TABLE_TWO_ROWS)

    assert decide_footprint(
        before,
        after,
        {"Client ID": "CL-1002", "Client / Company Name": "Everest"},
        ["Client / Company Name", "Client ID"],
        preferred="Client ID",
    ) == ["Client ID"]


def test_the_footprint_is_empty_when_nothing_new_is_visible() -> None:
    before = _inspection(*FORM)
    after = _inspection(*(FORM + TABLE_ROW_ONE))

    assert decide_footprint(
        before, after, {"Client ID": "CL-1002"}, ["Client ID"], preferred="Client ID"
    ) == []


def test_the_footprint_needs_an_after_screen() -> None:
    assert decide_footprint(None, None, {"Client ID": "CL-1002"}, ["Client ID"]) == []


def test_unreadable_screens_leave_the_save_unknown() -> None:
    result = check_row_appeared(None, None, {"Client ID": "CL-1002"}, ["Client ID"])

    assert result.outcome == UNKNOWN
