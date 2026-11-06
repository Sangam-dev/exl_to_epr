"""Unit tests for secret redaction (Segment 0)."""

from __future__ import annotations

from excel_agent.logging_setup import REDACTED, mask_secret, redact


def test_redacts_env_style_api_key() -> None:
    text = "GEMINI_API_KEY=AQ.abcdefghijklmnop12345"

    cleaned = redact(text)

    assert "AQ.abcdefghijklmnop12345" not in cleaned
    assert REDACTED in cleaned


def test_redacts_json_style_api_key() -> None:
    text = '{"api_key": "super-secret-value-123"}'

    cleaned = redact(text)

    assert "super-secret-value-123" not in cleaned
    assert REDACTED in cleaned


def test_redacts_google_api_key() -> None:
    text = "using AIzaSyA1234567890abcdefghijklmnopqrstu now"

    cleaned = redact(text)

    assert "AIzaSyA1234567890abcdefghijklmnopqrstu" not in cleaned
    assert REDACTED in cleaned


def test_leaves_normal_text_alone() -> None:
    text = "Filled 3 fields and clicked Save"

    assert redact(text) == text


def test_mask_secret_never_reveals_full_value() -> None:
    masked = mask_secret("AQ.abcdefghijklmnop")

    assert masked == "AQ.a...mnop"
    assert "cdefghijkl" not in masked


def test_mask_secret_handles_missing_value() -> None:
    assert mask_secret("") == "<missing>"
