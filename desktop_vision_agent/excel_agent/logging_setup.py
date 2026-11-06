"""Logging setup with secret redaction.

Every log record passes through :class:`RedactionFilter`, so API keys and
tokens cannot leak into the console or log files by accident.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_KEY = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"

_PATTERNS = (
    # key=value, key: value, "api_key": "value"
    re.compile(
        rf"(?i)({_SECRET_KEY}\s*[\"']?\s*[=:]\s*[\"']?)([^\s\"',}}]+)"
    ),
    # Google API keys (AIza...) and Gemini-style keys (AQ....)
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-.]{10,}\b"),
)

REDACTED = "<REDACTED>"


def redact(text: str) -> str:
    """Return *text* with credential-looking substrings replaced."""
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        if match.lastindex and match.lastindex >= 2:
            return f"{match.group(1)}{REDACTED}"
        return REDACTED

    for pattern in _PATTERNS:
        text = pattern.sub(_replace, text)
    return text


def mask_secret(value: str, visible: int = 4) -> str:
    """Return a safe display form of a secret (never the full value)."""
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


class RedactionFilter(logging.Filter):
    """Scrub secrets from formatted log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # never let logging fail because of redaction
            return True

        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: str | Path | None = None,
    console: bool = True,
) -> logging.Logger:
    """Configure root logging once and return the project logger."""
    logger = logging.getLogger("excel_agent")
    root = logging.getLogger()

    if getattr(root, "_excel_agent_configured", False):
        return logger

    resolved_level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(resolved_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(RedactionFilter())
        root.addHandler(stream)

    directory = Path(log_dir) if log_dir is not None else (
        Path(__file__).resolve().parents[1] / "data" / "logs"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / "agent.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactionFilter())
        root.addHandler(file_handler)
    except OSError:
        # A read-only or unavailable log directory must not stop the agent.
        logger.warning("file logging unavailable at %s", directory)

    root._excel_agent_configured = True  # type: ignore[attr-defined]
    return logger
