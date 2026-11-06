"""Shared pytest setup."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def valid_config() -> dict:
    """A minimal configuration that should always validate."""
    return {
        "app": {"title_contains": "Test Form"},
        "excel": {
            "input_path": "data/input/sample.xlsx",
            "output_path": "data/output/sample_agent.xlsx",
            "sheet": "Rows",
            "header_row": 1,
            "id_column": "ID",
        },
        "fields": [
            {"column": "Name", "field": "Customer Name", "type": "text"},
            {"column": "Amount", "field": "Amount", "type": "number"},
        ],
        "actions": {"save_button": "Save"},
        "gemini": {"model": "gemini-3.6-flash"},
        "safety": {"max_attempts": 3},
    }
