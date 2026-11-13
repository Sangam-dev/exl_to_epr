"""Unit tests for the calibration helpers (Segment 6)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from excel_agent.agent import calibration
from excel_agent.agent.calibration import (
    ResolvedLayout,
    ResolvedTarget,
    SaveEvidence,
    calibrate_row,
    compare_saved_fields,
    read_back,
    resolve_layout,
    values_match,
)
from excel_agent.config import AgentConfig
from excel_agent.excel.reader import ExcelRow
from excel_agent.models.verification import (
    PostSaveResponse,
    PostSaveVerdict,
    PreSaveResponse,
    PreSaveVerdict,
)
from excel_agent.ai.mapping import (
    ApprovedMapping,
    ButtonEntry,
    MappingEntry,
    mark_trusted,
    load_mapping,
)
from excel_agent.desktop.capture import Capture
from excel_agent.desktop.uia import UIAInspection
from excel_agent.models.screen import Rect, UIACandidate, WindowInfo


def _window(width: int = 400, height: int = 300) -> WindowInfo:
    return WindowInfo(
        handle=1,
        title="Form",
        process_name="test.exe",
        class_name="Form",
        rect=Rect(0, 0, width, height),
        client_rect=Rect(0, 0, width, height),
        dpi=96,
    )


def _candidate(
    candidate_id: str,
    name: str,
    *,
    control_type: str = "Edit",
    rect: tuple[int, int, int, int] = (10, 20, 110, 40),
) -> UIACandidate:
    return UIACandidate(
        candidate_id=candidate_id,
        name=name,
        control_type=control_type,
        automation_id="",
        class_name="",
        rect=Rect(*rect),
        value="",
        enabled=True,
    )


def _inspection(candidates: list[UIACandidate], *, width=400, height=300) -> UIAInspection:
    return UIAInspection(
        window=_window(width, height),
        candidates=tuple(candidates),
        _controls={},
    )


def _entry(
    column: str = "Invoice Number",
    field_id: str = "f1",
    *,
    label: str = "Invoice Number",
    type_name: str = "text",
    candidate_id: str | None = "W1",
    rect: tuple[int, int, int, int] = (10, 20, 110, 40),
) -> MappingEntry:
    return MappingEntry(
        column=column,
        field_id=field_id,
        label=label,
        type=type_name,
        uia_candidate_id=candidate_id,
        rect=rect,
        confidence=1.0,
    )


def _save_button(
    *,
    label: str = "Save",
    candidate_id: str | None = "W9",
    rect: tuple[int, int, int, int] = (200, 200, 260, 240),
) -> ButtonEntry:
    return ButtonEntry(
        button_id="b1",
        label=label,
        role="save",
        uia_candidate_id=candidate_id,
        rect=rect,
    )


def _mapping(
    entries: list[MappingEntry] | None = None,
    buttons: list[ButtonEntry] | None = None,
) -> ApprovedMapping:
    return ApprovedMapping(
        approved_at="2026-11-08T12:00:00+00:00",
        entries=entries if entries is not None else [_entry()],
        buttons=buttons if buttons is not None else [_save_button()],
    )


# ---------------------------------------------------------------------------
# Layout resolution
# ---------------------------------------------------------------------------


def test_resolve_layout_uses_the_live_uia_id() -> None:
    inspection = _inspection(
        [
            _candidate("W1", "Invoice Number", rect=(20, 30, 120, 50)),
            _candidate("W9", "Save", control_type="Button", rect=(210, 210, 270, 250)),
        ]
    )

    layout = resolve_layout(_mapping(), inspection)

    assert layout.problems == ()
    assert layout.fields["Invoice Number"].rect == Rect(20, 30, 120, 50)
    assert layout.fields["Invoice Number"].source == "uia-id"
    assert layout.save is not None
    assert layout.save.rect == Rect(210, 210, 270, 250)


def test_resolve_layout_falls_back_to_the_control_name() -> None:
    inspection = _inspection(
        [
            _candidate("W1", "Something Else", rect=(20, 30, 120, 50)),
            _candidate("W2", "Invoice Number", rect=(30, 40, 130, 60)),
            _candidate("W9", "Save", control_type="Button", rect=(210, 210, 270, 250)),
        ]
    )

    layout = resolve_layout(_mapping(), inspection)

    assert layout.fields["Invoice Number"].rect == Rect(30, 40, 130, 60)
    assert layout.fields["Invoice Number"].source == "uia-name"
    assert any("now names" in warning for warning in layout.warnings)


def test_resolve_layout_falls_back_to_the_saved_rectangle() -> None:
    inspection = _inspection(
        [
            _candidate("W9", "Save", control_type="Button", rect=(210, 210, 270, 250)),
        ]
    )

    layout = resolve_layout(_mapping(), inspection)

    assert layout.fields["Invoice Number"].rect == Rect(10, 20, 110, 40)
    assert layout.fields["Invoice Number"].source == "mapping"
    assert any("saved rectangle" in warning for warning in layout.warnings)


def test_resolve_layout_rejects_a_saved_rectangle_outside_the_window() -> None:
    inspection = _inspection(
        [
            _candidate("W9", "Save", control_type="Button", rect=(210, 210, 270, 250)),
        ]
    )
    mapping = _mapping([_entry(rect=(900, 900, 1000, 1000))])

    layout = resolve_layout(mapping, inspection)

    assert layout.problems
    assert any("outside the current window" in problem for problem in layout.problems)


def test_resolve_layout_requires_a_save_button() -> None:
    inspection = _inspection([])
    mapping = _mapping(buttons=[])

    layout = resolve_layout(mapping, inspection)

    assert layout.save is None
    assert any("role 'save'" in problem for problem in layout.problems)


# ---------------------------------------------------------------------------
# Read-back
# ---------------------------------------------------------------------------


class _Value:
    def __init__(self, value: str) -> None:
        self.CurrentValue = value


class _Control:
    def __init__(self, value: str) -> None:
        self.iface_value = _Value(value)


def _inspection_with_values(values: dict[str, str]) -> UIAInspection:
    candidates = [
        _candidate(candidate_id, name)
        for candidate_id, name in values.items()
    ]
    controls = {
        candidate_id: _Control(value)
        for candidate_id, value in values.items()
    }
    return UIAInspection(
        window=_window(),
        candidates=tuple(candidates),
        _controls=controls,
    )


def _layout_with(field_ids: dict[str, str | None]) -> ResolvedLayout:
    fields = {
        column: ResolvedTarget(
            key=column,
            rect=Rect(10, 20, 110, 40),
            candidate_id=candidate_id,
            source="uia-id",
            field_type="text",
        )
        for column, candidate_id in field_ids.items()
    }
    return ResolvedLayout(fields=fields, save=None)


def test_read_back_accepts_matching_values() -> None:
    inspection = _inspection_with_values({"W1": "INV-0001"})
    layout = _layout_with({"Invoice Number": "W1"})

    problems, warnings = read_back(
        inspection, layout, {"Invoice Number": "INV-0001"}
    )

    assert problems == []
    assert warnings == []


def test_read_back_reports_a_mismatch() -> None:
    inspection = _inspection_with_values({"W1": "WRONG"})
    layout = _layout_with({"Invoice Number": "W1"})

    problems, _ = read_back(
        inspection, layout, {"Invoice Number": "INV-0001"}
    )

    assert any("read back 'WRONG'" in problem for problem in problems)


def test_read_back_skips_fields_without_a_uia_candidate() -> None:
    inspection = _inspection_with_values({})
    layout = _layout_with({"Invoice Number": None})

    problems, warnings = read_back(
        inspection, layout, {"Invoice Number": "INV-0001"}
    )

    assert problems == []
    assert warnings == []


def test_values_match_handles_text_and_numbers() -> None:
    assert values_match("  INV-0001 ", "INV-0001", "text")
    assert values_match("1,234.50", "1234.5", "number")
    assert not values_match("1234", "1235", "number")
    assert not values_match("abc", "1234.5", "number")
    assert not values_match("INV-0002", "INV-0001", "text")


# ---------------------------------------------------------------------------
# Post-save pixel comparison
# ---------------------------------------------------------------------------


def _capture(
    *,
    width: int = 200,
    height: int = 100,
    text_box: tuple[int, int, int, int] | None = None,
) -> Capture:
    image = Image.new("RGB", (width, height), "white")
    if text_box is not None:
        draw = ImageDraw.Draw(image)
        draw.rectangle(text_box, fill="black")
    return Capture(
        image=image,
        origin=(0, 0),
        scale_x=1.0,
        scale_y=1.0,
        window=_window(width, height),
    )


def _comparison_layout() -> ResolvedLayout:
    return ResolvedLayout(
        fields={
            "Invoice Number": ResolvedTarget(
                key="Invoice Number",
                rect=Rect(10, 10, 90, 30),
                candidate_id="W1",
                source="uia-id",
            ),
            "Notes": ResolvedTarget(
                key="Notes",
                rect=Rect(10, 50, 90, 70),
                candidate_id="W2",
                source="uia-id",
            ),
        },
        save=None,
    )


def test_compare_saved_fields_detects_unchanged_text() -> None:
    pre = _capture(text_box=(12, 12, 40, 28))
    post = _capture(text_box=(12, 12, 40, 28))

    evidence = compare_saved_fields(
        pre,
        post,
        _comparison_layout(),
        {"Invoice Number": "INV-0001", "Notes": ""},
    )

    assert evidence.unchanged
    assert evidence.compared == ("Invoice Number",)
    assert evidence.identical == ("Invoice Number",)


def test_compare_saved_fields_detects_cleared_text() -> None:
    pre = _capture(text_box=(12, 12, 40, 28))
    post = _capture()

    evidence = compare_saved_fields(
        pre,
        post,
        _comparison_layout(),
        {"Invoice Number": "INV-0001", "Notes": ""},
    )

    assert not evidence.unchanged
    assert evidence.compared == ("Invoice Number",)


def test_compare_saved_fields_ignores_empty_fields() -> None:
    pre = _capture(text_box=(12, 12, 40, 28))
    post = _capture(text_box=(12, 12, 40, 28))

    evidence = compare_saved_fields(
        pre,
        post,
        _comparison_layout(),
        {"Invoice Number": "INV-0001", "Notes": ""},
    )

    assert "Notes" not in evidence.compared


def test_compare_saved_fields_handles_a_size_change() -> None:
    evidence = compare_saved_fields(
        _capture(),
        _capture(width=300),
        _comparison_layout(),
        {"Invoice Number": "INV-0001"},
    )

    assert not evidence.unchanged
    assert "size changed" in evidence.detail


def test_compare_saved_fields_reports_when_nothing_can_be_compared() -> None:
    evidence = compare_saved_fields(
        _capture(),
        _capture(),
        _comparison_layout(),
        {"Invoice Number": "", "Notes": ""},
    )

    assert not evidence.unchanged
    assert "no non-empty field" in evidence.detail


# ---------------------------------------------------------------------------
# Trust persistence
# ---------------------------------------------------------------------------


def test_mark_trusted_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"

    trusted = mark_trusted(_mapping(), path)
    assert trusted.trusted
    assert trusted.trusted_at

    loaded = load_mapping(path)
    assert loaded.trusted
    assert loaded.trusted_at == trusted.trusted_at

    untrusted = mark_trusted(loaded, path, trusted=False)
    assert not untrusted.trusted
    assert untrusted.trusted_at == ""
    assert not load_mapping(path).trusted


# ---------------------------------------------------------------------------
# Calibration loop (desktop and Gemini stubbed out)
# ---------------------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    def write_status(
        self, row_number: int, status: str, error: str = "", timestamp=None
    ) -> None:
        self.calls.append((row_number, status, error))


def _config(max_attempts: int = 3) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "app": {"title_contains": "Form"},
            "excel": {
                "input_path": "data/input/in.xlsx",
                "output_path": "data/output/out.xlsx",
                "sheet": "Rows",
                "id_column": "Invoice Number",
            },
            "fields": [],
            "actions": {"save_button": "Save", "wait_after_save_seconds": 0},
            "safety": {"max_attempts": max_attempts, "action_delay_seconds": 0},
        }
    )


def _pre_verdict(approved: bool, issues: tuple[str, ...] = ()) -> PreSaveVerdict:
    return PreSaveVerdict(
        approved=approved,
        response=PreSaveResponse(approved=approved),
        issues=issues,
    )


def _post_verdict(
    approved: bool, saved: bool, issues: tuple[str, ...] = ()
) -> PostSaveVerdict:
    return PostSaveVerdict(
        approved=approved,
        saved=saved,
        response=PostSaveResponse(approved=approved, saved=saved),
        issues=issues,
    )


def _stub_desktop(monkeypatch, *, pre_verdict, post_verdict, evidence):
    window = _window()
    inspection = UIAInspection(
        window=window,
        candidates=(),
        _controls={},
    )
    captures: list[Capture] = [_capture(), _capture()]
    calls = {"pre": 0, "post": 0, "save": 0, "capture": 0}

    def fake_capture(_window):
        index = min(calls["capture"], len(captures) - 1)
        calls["capture"] += 1
        return captures[index]

    def fake_pre(*_args, **_kwargs):
        calls["pre"] += 1
        return pre_verdict

    def fake_post(*_args, **_kwargs):
        calls["post"] += 1
        return post_verdict

    def fake_save(_layout):
        calls["save"] += 1

    monkeypatch.setattr(calibration, "attach_window", lambda _app: window)
    monkeypatch.setattr(calibration, "inspect_window", lambda _window: inspection)
    monkeypatch.setattr(calibration, "fill_row", lambda *a, **k: None)
    monkeypatch.setattr(
        calibration, "read_back", lambda *a, **k: ([], [])
    )
    monkeypatch.setattr(calibration, "capture_window", fake_capture)
    monkeypatch.setattr(calibration, "check_pre_save", fake_pre)
    monkeypatch.setattr(calibration, "check_post_save", fake_post)
    monkeypatch.setattr(calibration, "_click_save", fake_save)
    monkeypatch.setattr(calibration, "_wait", lambda *a, **k: True)
    monkeypatch.setattr(
        calibration, "compare_saved_fields", lambda *a, **k: evidence
    )
    return calls


def _row() -> ExcelRow:
    return ExcelRow(
        row_number=2,
        values={"Invoice Number": "INV-0001"},
    )


def test_calibrate_row_marks_done_and_trusts_the_map(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        pre_verdict=_pre_verdict(True),
        post_verdict=_post_verdict(True, True),
        evidence=SaveEvidence(unchanged=False, detail="changed"),
    )
    writer = FakeWriter()
    mapping_path = tmp_path / "mapping.json"

    result = calibrate_row(
        _row(),
        config=_config(),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        mapping_path=mapping_path,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "DONE"
    assert result.trusted
    assert result.attempts == 1
    assert writer.calls == [(2, "DONE", "")]
    assert calls["save"] == 1
    assert calls["pre"] == 1
    assert calls["post"] == 1
    assert load_mapping(mapping_path).trusted
    assert len(result.screenshots) == 2


def test_calibrate_row_rechecks_when_pre_save_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        pre_verdict=_pre_verdict(False, ("the value is missing",)),
        post_verdict=_post_verdict(True, True),
        evidence=SaveEvidence(unchanged=False),
    )
    writer = FakeWriter()
    mapping_path = tmp_path / "mapping.json"

    result = calibrate_row(
        _row(),
        config=_config(max_attempts=2),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        mapping_path=mapping_path,
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert not result.trusted
    assert result.attempts == 2
    assert calls["save"] == 0
    assert calls["pre"] == 2
    assert calls["post"] == 0
    assert writer.calls[0][1] == "RECHECK"
    assert "pre-save rejected" in writer.calls[0][2]
    assert not load_mapping(mapping_path).trusted


def test_calibrate_row_does_not_retry_an_uncertain_save(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        pre_verdict=_pre_verdict(True),
        post_verdict=_post_verdict(False, True, ("the evidence is ambiguous",)),
        evidence=SaveEvidence(unchanged=False, detail="changed"),
    )
    writer = FakeWriter()

    result = calibrate_row(
        _row(),
        config=_config(max_attempts=3),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        mapping_path=tmp_path / "mapping.json",
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert calls["pre"] == 1
    assert calls["post"] == 1
    assert "may already exist" in writer.calls[0][2]


def test_calibrate_row_does_not_save_again_when_the_fields_are_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _stub_desktop(
        monkeypatch,
        pre_verdict=_pre_verdict(True),
        post_verdict=_post_verdict(True, True),
        evidence=SaveEvidence(
            unchanged=True,
            compared=("Invoice Number",),
            identical=("Invoice Number",),
        ),
    )
    writer = FakeWriter()

    result = calibrate_row(
        _row(),
        config=_config(max_attempts=2),
        mapping=_mapping(),
        writer=writer,
        client=object(),
        mapping_path=tmp_path / "mapping.json",
        screenshot_dir=tmp_path / "shots",
    )

    assert result.status == "RECHECK"
    assert result.attempts == 1
    assert calls["save"] == 1
    assert calls["post"] == 1
    assert "unchanged" in writer.calls[0][2]
    assert "may already exist" in writer.calls[0][2]
