"""Pluggable Save confirmation (Segment 10).

A Save is only ``CONFIRMED`` when configured success text is read from the
target window. Error text can ``REJECT`` it. Everything else is ``UNKNOWN``.

Pixel changes may be passed in as a *weak hint* for the log, but they are never
evidence on their own and cannot produce ``CONFIRMED``.

The text reader is injectable: ``GeminiTextReader`` is the only real
implementation for now. An OCR reader can be added in Segment 3 without
changing the strategy or the callers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from ..ai.client import GeminiClient
from ..ai.prompts import load_prompt
from ..config import ConfirmationConfig, NormalizedRegion

CONFIRMED = "confirmed"
REJECTED = "rejected"
UNKNOWN = "unknown"

_BOX = tuple[int, int, int, int]


class ConfirmationError(RuntimeError):
    """The confirmation strategy could not read or interpret the evidence."""


@dataclass(frozen=True)
class ConfirmationResult:
    """One explicit confirmation outcome."""

    outcome: str
    reason: str = ""
    evidence: tuple[str, ...] = ()
    source: str = ""


class TextReader(Protocol):
    """Read the text visible in one cropped window region."""

    def read_text(self, image: Image.Image) -> str:
        """Return the text found in *image*."""


class SaveConfirmer(Protocol):
    """Classify the post-Save screen into CONFIRMED, REJECTED, or UNKNOWN."""

    def confirm(
        self, image: Image.Image, *, weak_hint: str = ""
    ) -> ConfirmationResult:
        """Return the explicit outcome for this saved row."""


class GeminiTextReader:
    """Read region text with Gemini vision.

    This is the only real reader in this segment; OCR is deliberately left as
    an interface for a later segment. A missing or unusable response raises
    :class:`ConfirmationError`, which the strategy turns into ``UNKNOWN``.
    """

    name = "gemini"

    def __init__(
        self, client: GeminiClient, *, prompt_name: str = "read_region_text"
    ) -> None:
        self._client = client
        self._prompt_name = prompt_name

    def read_text(self, image: Image.Image) -> str:
        prompt = load_prompt(self._prompt_name)
        response = self._client.generate_json(prompt, image)
        text = response.data.get("text")
        if not isinstance(text, str):
            raise ConfirmationError(
                "the Gemini text reader returned no 'text' string"
            )
        return text


def _box_for(image: Image.Image, region: NormalizedRegion | None) -> _BOX:
    if region is None:
        return (0, 0, image.width, image.height)
    return region.to_pixels(image.width, image.height)


class TextConfirmation:
    """Match configured text patterns against cropped window regions."""

    def __init__(
        self, config: ConfirmationConfig, reader: TextReader
    ) -> None:
        self._config = config
        self._reader = reader
        self._success = (
            re.compile(config.success_pattern)
            if config.success_pattern
            else None
        )
        self._errors = tuple(
            (pattern, re.compile(pattern))
            for pattern in config.error_patterns
        )

    @property
    def source(self) -> str:
        return getattr(self._reader, "name", type(self._reader).__name__)

    def _read(self, image: Image.Image, box: _BOX) -> str:
        left, top, right, bottom = box
        crop = image.crop((left, top, right, bottom))
        return self._reader.read_text(crop)

    def _read_regions(self, image: Image.Image) -> tuple[str, str]:
        """Read each distinct region once; return ``(success, error)`` text."""
        texts: dict[_BOX, str] = {}

        def read(box: _BOX) -> str:
            if box not in texts:
                texts[box] = self._read(image, box)
            return texts[box]

        success_text = ""
        error_text = ""
        if self._success is not None:
            success_text = read(
                _box_for(image, self._config.success_region)
            )
        if self._errors:
            error_text = read(_box_for(image, self._config.error_region))
        return success_text, error_text

    def confirm(
        self, image: Image.Image, *, weak_hint: str = ""
    ) -> ConfirmationResult:
        """Classify the post-Save screen as CONFIRMED, REJECTED, or UNKNOWN."""
        evidence: list[str] = []
        if weak_hint:
            evidence.append(f"weak hint: {weak_hint}")

        if self._success is None and not self._errors:
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=(
                    "no success or error pattern is configured, so the Save "
                    "cannot be confirmed"
                ),
                evidence=tuple(evidence),
                source=self.source,
            )

        try:
            success_text, error_text = self._read_regions(image)
        except Exception as exc:  # reader/transport failure is never a save
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=f"reader failed: {exc}",
                evidence=tuple(evidence),
                source=self.source,
            )

        matched_patterns: list[str] = []
        matched_text: list[str] = []
        for pattern, regex in self._errors:
            match = regex.search(error_text)
            if match is not None:
                matched_patterns.append(pattern)
                text = match.group(0).strip()
                if text and text not in matched_text:
                    matched_text.append(text)

        if matched_patterns:
            reason = "error text matched " + ", ".join(
                repr(pattern) for pattern in matched_patterns
            )
            if matched_text:
                reason += " (" + ", ".join(
                    repr(text) for text in matched_text
                ) + ")"
            return ConfirmationResult(
                outcome=REJECTED,
                reason=reason,
                evidence=tuple(evidence),
                source=self.source,
            )

        if self._success is None:
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=(
                    "no error text matched, but no success pattern is "
                    "configured, so the Save cannot be confirmed"
                ),
                evidence=tuple(evidence),
                source=self.source,
            )

        match = self._success.search(success_text)
        if match is None:
            return ConfirmationResult(
                outcome=UNKNOWN,
                reason=(
                    "success text did not contain "
                    f"{self._config.success_pattern!r}"
                ),
                evidence=tuple(evidence),
                source=self.source,
            )

        return ConfirmationResult(
            outcome=CONFIRMED,
            reason=f"success text matched {self._config.success_pattern!r}",
            evidence=tuple(evidence),
            source=self.source,
        )


__all__ = [
    "CONFIRMED",
    "REJECTED",
    "UNKNOWN",
    "ConfirmationError",
    "ConfirmationResult",
    "GeminiTextReader",
    "SaveConfirmer",
    "TextConfirmation",
    "TextReader",
]
