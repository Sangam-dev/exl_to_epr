"""Declarative actions for the static, step-driven form filler.

A static form is described as an ordered list of steps. Only two actions are
supported, on purpose: click a named button, and fill a named text field.
Dropdowns, date pickers and grids need control-specific interaction, so they
stay out of this model until there is a proven way to drive them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("step names must not be empty")
    return value


class _Step(BaseModel):
    """Fields shared by every step."""

    model_config = ConfigDict(extra="forbid")

    optional: bool = False


class ClickStep(_Step):
    """Click a button, matched by its UIA name."""

    action: Literal["click"]
    button: str

    @field_validator("button")
    @classmethod
    def _named(cls, value: str) -> str:
        return _clean(value)


class FillStep(_Step):
    """Type one Excel column's value into one text field.

    ``field`` overrides the UIA label when the same column has to go into a
    differently named control; by default the label comes from ``fields``.
    """

    action: Literal["fill"]
    column: str
    field: str | None = None

    @field_validator("column")
    @classmethod
    def _column(cls, value: str) -> str:
        return _clean(value)

    @field_validator("field")
    @classmethod
    def _field(cls, value: str | None) -> str | None:
        return None if value is None else _clean(value)


FormStep = Annotated[Union[ClickStep, FillStep], Field(discriminator="action")]


__all__ = ["ClickStep", "FillStep", "FormStep"]
