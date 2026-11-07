"""Unit tests for configuration loading and validation (Segment 0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from excel_agent.config import (
    AgentConfig,
    ConfigError,
    load_config,
    project_root,
    resolve_path,
)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_config_loads(valid_config: dict, tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, valid_config))

    assert isinstance(config, AgentConfig)
    assert config.app.title_contains == "Test Form"
    assert config.excel.status_column == "Agent Status"
    assert len(config.fields) == 2
    assert config.gemini.model == "gemini-3.6-flash"


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "does-not-exist.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(path)


def test_duplicate_column_is_rejected(valid_config: dict, tmp_path: Path) -> None:
    valid_config["fields"].append(
        {"column": "Name", "field": "Other Field", "type": "text"}
    )

    with pytest.raises(ConfigError, match="only one field"):
        load_config(_write(tmp_path, valid_config))


def test_duplicate_field_is_rejected(valid_config: dict, tmp_path: Path) -> None:
    valid_config["fields"].append(
        {"column": "Other Column", "field": "Amount", "type": "number"}
    )

    with pytest.raises(ConfigError, match="only one column"):
        load_config(_write(tmp_path, valid_config))


def test_unknown_key_is_rejected(valid_config: dict, tmp_path: Path) -> None:
    valid_config["unexpected"] = True

    with pytest.raises(ConfigError, match="invalid config"):
        load_config(_write(tmp_path, valid_config))


def test_unsupported_field_type_is_rejected(valid_config: dict) -> None:
    valid_config["fields"][0]["type"] = "dropdown"

    with pytest.raises(ValidationError):
        AgentConfig.model_validate(valid_config)


def test_hotkeys_are_normalised(valid_config: dict) -> None:
    valid_config["safety"] = {"stop_hotkey": " ALT+T ", "pause_hotkey": "Alt+P"}

    config = AgentConfig.model_validate(valid_config)

    assert config.safety.stop_hotkey == "alt+t"
    assert config.safety.pause_hotkey == "alt+p"


def test_app_requires_a_matcher(valid_config: dict) -> None:
    valid_config["app"] = {"title_contains": "", "process_name": None}

    with pytest.raises(ValidationError, match="title_contains or app.process_name"):
        AgentConfig.model_validate(valid_config)


def test_hotkeys_must_be_distinct(valid_config: dict) -> None:
    valid_config["safety"] = {"stop_hotkey": "alt+t", "pause_hotkey": "alt+t"}

    with pytest.raises(ValidationError, match="different hotkeys"):
        AgentConfig.model_validate(valid_config)


def test_resolve_path_uses_project_root() -> None:
    assert resolve_path("data/input/x.xlsx") == project_root() / "data" / "input" / "x.xlsx"
    absolute = project_root() / "data" / "input" / "x.xlsx"
    assert resolve_path(absolute) == absolute


def test_mapped_column_cannot_be_a_status_column(valid_config: dict) -> None:
    valid_config["fields"].append(
        {"column": "Agent Status", "field": "Status", "type": "text"}
    )

    with pytest.raises(ValidationError, match="collide with agent status columns"):
        AgentConfig.model_validate(valid_config)


def test_id_column_cannot_be_a_status_column(valid_config: dict) -> None:
    valid_config["excel"]["id_column"] = "Agent Status"

    with pytest.raises(ValidationError, match="id_column"):
        AgentConfig.model_validate(valid_config)
