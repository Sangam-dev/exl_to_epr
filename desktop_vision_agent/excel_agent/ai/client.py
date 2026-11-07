"""Gemini client with retries, JSON extraction, and request logging (Segment 4).

The API key is read from ``GEMINI_API_KEY`` only. Every call is logged to
``data/ai_logs`` with credential-looking text redacted, and the configured
call budget is enforced before each request.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..config import GeminiConfig, get_gemini_api_key, project_root
from ..logging_setup import redact

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class GeminiError(RuntimeError):
    """Gemini could not produce a usable response."""


class AIResponseError(GeminiError):
    """The model response was not a JSON object."""


@dataclass(frozen=True)
class AIResponse:
    """One successful model response and where it was logged."""

    data: dict[str, Any]
    text: str
    attempts: int
    log_path: Path | None


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object found in *text*.

    Accepts a bare object, a fenced ```json block, or an object embedded in
    surrounding prose.
    """
    if not text or not text.strip():
        raise AIResponseError("the model returned an empty response")

    candidate = text.strip()
    fence = _FENCE_RE.match(candidate)
    if fence is not None:
        candidate = fence.group(1).strip()

    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise AIResponseError("no JSON object found in the model response")
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIResponseError(f"invalid JSON in the model response: {exc}") from exc

    if not isinstance(value, dict):
        raise AIResponseError("the model response must be a JSON object")
    return value


def _image_part(image: Image.Image) -> Any:
    from google.genai import types

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/png")


class GeminiClient:
    """Call Gemini and return parsed JSON.

    ``client`` and ``sleep`` are injectable so unit tests never touch the
    network or wait for real backoff delays.
    """

    def __init__(
        self,
        config: GeminiConfig,
        *,
        api_key: str | None = None,
        log_dir: str | Path | None = None,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._calls = 0
        self._log_dir = (
            Path(log_dir)
            if log_dir is not None
            else project_root() / "data" / "ai_logs"
        )

        if client is None:
            from google import genai
            from google.genai import types

            key = (api_key if api_key is not None else get_gemini_api_key()).strip()
            if not key:
                raise GeminiError(
                    "GEMINI_API_KEY is not set; add it to .env or the environment"
                )
            client = genai.Client(
                api_key=key,
                http_options=types.HttpOptions(
                    timeout=config.timeout_seconds * 1000
                ),
            )
        self._client = client

    @property
    def calls(self) -> int:
        """Number of API requests attempted, including retries."""
        return self._calls

    def generate_json(
        self, prompt: str, image: Image.Image | None = None
    ) -> AIResponse:
        """Send one prompt (and optional image) and return parsed JSON."""
        from google.genai import types

        contents: list[Any] = []
        if image is not None:
            contents.append(_image_part(image))
        contents.append(prompt)

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 2):
            if self._calls >= self._config.max_ai_calls:
                raise GeminiError(
                    f"AI call budget exhausted ({self._config.max_ai_calls} calls)"
                )
            self._calls += 1

            started = time.monotonic()
            try:
                response = self._client.models.generate_content(
                    model=self._config.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                text = getattr(response, "text", None) or ""
                data = extract_json(text)
            except Exception as exc:
                last_error = exc
                self._log(
                    status="error",
                    attempt=attempt,
                    prompt=prompt,
                    image=image,
                    duration=time.monotonic() - started,
                    error=str(exc),
                )
                if attempt <= self._config.max_retries:
                    delay = min(2 ** (attempt - 1), 8)
                    logger.warning(
                        "Gemini call failed (attempt %s/%s): %s; retrying in %ss",
                        attempt,
                        self._config.max_retries + 1,
                        exc,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise GeminiError(
                    f"Gemini failed after {attempt} attempt(s): {exc}"
                ) from exc

            log_path = self._log(
                status="ok",
                attempt=attempt,
                prompt=prompt,
                image=image,
                duration=time.monotonic() - started,
                text=text,
                data=data,
            )
            return AIResponse(
                data=data,
                text=text,
                attempts=attempt,
                log_path=log_path,
            )

        # Unreachable: the loop either returns or raises.
        raise GeminiError(f"Gemini failed: {last_error}")

    def _log(
        self,
        *,
        status: str,
        attempt: int,
        prompt: str,
        image: Image.Image | None,
        duration: float,
        text: str = "",
        data: dict[str, Any] | None = None,
        error: str = "",
    ) -> Path | None:
        payload = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": self._config.model,
            "attempt": attempt,
            "status": status,
            "duration_ms": int(duration * 1000),
            "image": (
                {"width": image.width, "height": image.height}
                if image is not None
                else None
            ),
            "prompt": redact(prompt),
            "response_text": redact(text)[:20000],
            "data": data,
            "error": redact(error),
        }
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._log_dir / f"{stamp}_{self._calls:03d}_{status}.json"
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("could not write the AI log to %s", self._log_dir)
            return None
        return path
