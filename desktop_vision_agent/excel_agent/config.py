"""Typed configuration for the Excel desktop agent.

Configuration lives in JSON (see ``config/agent.example.json``). Secrets never
live in JSON: the Gemini API key is read from the environment, normally via
``desktop_vision_agent/.env``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FieldType = Literal["text", "number"]          #typealias for field types
EmptyCellPolicy = Literal["skip", "clear"]


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


def project_root() -> Path:
    """Return the ``desktop_vision_agent`` directory."""
    return Path(__file__).resolve().parents[1]


def resolve_path(value: str | Path) -> Path:
    """Resolve a config path relative to the project root."""
    path = Path(value)
    return path if path.is_absolute() else project_root() / path


class AppConfig(BaseModel):
    """Target application selection."""

    model_config = ConfigDict(extra="forbid")

    title_contains: str = ""
    process_name: str | None = None
    require_foreground: bool = True

    @model_validator(mode="after")
    def _must_have_matcher(self) -> "AppConfig":
        if not self.title_contains and not self.process_name:
            raise ValueError("app.title_contains or app.process_name must be set")
        return self


class ExcelConfig(BaseModel):
    """Workbook layout and status columns."""

    model_config = ConfigDict(extra="forbid")

    input_path: str
    output_path: str
    sheet: str | int = 0
    header_row: int = Field(default=1, ge=1)
    id_column: str
    status_column: str = "Agent Status"
    timestamp_column: str = "Agent Timestamp"
    error_column: str = "Agent Error"
    done_value: str = "DONE"
    recheck_value: str = "RECHECK"
    skip_statuses: list[str] = Field(default_factory=lambda: ["DONE"])
    empty_cell_policy: EmptyCellPolicy = "skip"

    @field_validator("id_column", "status_column", "timestamp_column", "error_column")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("column names must not be empty")
        return value


class FieldConfig(BaseModel):
    """One approved Excel column -> form field mapping."""

    model_config = ConfigDict(extra="forbid")

    column: str
    field: str
    type: FieldType = "text"

    @field_validator("column", "field")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("mapping values must not be empty")
        return value


class ActionsConfig(BaseModel):
    """Buttons and timing for the target form."""

    model_config = ConfigDict(extra="forbid")

    save_button: str
    new_record_button: str | None = None
    wait_after_save_seconds: float = Field(default=1.5, ge=0)


class GeminiConfig(BaseModel):
    """Gemini model and cost guards."""

    model_config = ConfigDict(extra="forbid")

    model: str = "gemini-3.6-flash"
    max_image_dimension: int = Field(default=1600, ge=200)
    timeout_seconds: int = Field(default=60, ge=5)
    max_retries: int = Field(default=3, ge=0)
    max_ai_calls: int = Field(default=50, ge=0)

    @field_validator("model")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("gemini.model must not be empty")
        return value


class SafetyConfig(BaseModel):
    """Input lock and failure policy."""

    model_config = ConfigDict(extra="forbid")

    stop_hotkey: str = "alt+t"
    pause_hotkey: str = "alt+p"
    resume_hotkey: str = "alt+r"
    block_external_input: bool = True
    max_attempts: int = Field(default=3, ge=1)
    action_delay_seconds: float = Field(default=0.15, ge=0)
    stop_on_failure: bool = False

    @field_validator("stop_hotkey", "pause_hotkey", "resume_hotkey")
    @classmethod
    def _normalise_hotkey(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("hotkeys must not be empty")
        return value

    @model_validator(mode="after")
    def _distinct_hotkeys(self) -> "SafetyConfig":
        values = [self.stop_hotkey, self.pause_hotkey, self.resume_hotkey]
        if len(values) != len(set(values)):
            raise ValueError(
                "stop, pause, and resume must use different hotkeys: "
                + ", ".join(values)
            )
        return self


class AgentConfig(BaseModel):
    """Top-level configuration."""

    model_config = ConfigDict(extra="forbid")

    app: AppConfig
    excel: ExcelConfig
    fields: list[FieldConfig] = Field(default_factory=list)
    actions: ActionsConfig
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)

    @model_validator(mode="after")
    def _mapping_is_one_to_one(self) -> "AgentConfig":
        columns = [item.column for item in self.fields]
        fields = [item.field for item in self.fields]
        if len(columns) != len(set(columns)):
            raise ValueError("each Excel column may map to only one field")
        if len(fields) != len(set(fields)):
            raise ValueError("each form field may be mapped from only one column")

        reserved = {
            self.excel.status_column.strip().upper(),
            self.excel.timestamp_column.strip().upper(),
            self.excel.error_column.strip().upper(),
        }
        conflicts = [
            item.column for item in self.fields if item.column.strip().upper() in reserved
        ]
        if conflicts:
            raise ValueError(
                "mapped column(s) collide with agent status columns: "
                + ", ".join(repr(name) for name in conflicts)
            )
        if self.excel.id_column.strip().upper() in reserved:
            raise ValueError("excel.id_column must not be an agent status column")
        return self


def load_env(env_path: str | Path | None = None) -> Path:
    """Load ``.env`` without overriding already-set environment variables."""
    path = Path(env_path) if env_path is not None else project_root() / ".env"
    if not path.is_absolute():
        path = project_root() / path
    if path.is_file():
        load_dotenv(path, override=False)
    return path


def get_gemini_api_key() -> str:
    """Return the Gemini API key from the environment, or an empty string."""
    return os.getenv("GEMINI_API_KEY", "").strip()


def load_config(path: str | Path) -> AgentConfig:
    """Load and validate a JSON configuration file."""
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = project_root() / config_path

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc}") from exc

    try:
        return AgentConfig.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and nested ValueErrors
        raise ConfigError(f"invalid config {config_path}: {exc}") from exc
