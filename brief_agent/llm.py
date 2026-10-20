"""Thin wrapper around Gemini (google-genai): send a prompt, get back validated JSON.

Used for single structured calls. Multi-step agent work is orchestrated by Google ADK,
which runs on the same SDK.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

TRIAGE_MODEL = os.environ.get("BRIEF_TRIAGE_MODEL", "gemini-flash-lite-latest")
PLANNER_MODEL = os.environ.get("BRIEF_PLANNER_MODEL", "gemini-pro-latest")
# Tried in order when the planner model is unavailable (e.g. free-tier keys have no Pro quota).
PLANNER_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "BRIEF_PLANNER_FALLBACK_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
EXTRACT_MODEL = os.environ.get("BRIEF_EXTRACT_MODEL", "gemini-flash-latest")
EXTRACT_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "BRIEF_EXTRACT_FALLBACK_MODELS", "gemini-flash-lite-latest").split(",") if m.strip()]
# Separate, cheaper model for the stage 3 support check: its only job is to compare claim and quote.
SUPPORT_MODEL = os.environ.get("BRIEF_SUPPORT_MODEL", "gemini-flash-lite-latest")
# Stages 4–5 (aligning and judging across sources): the strongest available model first.
REASON_MODEL = os.environ.get("BRIEF_REASON_MODEL", "gemini-pro-latest")
REASON_FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "BRIEF_REASON_FALLBACK_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
MAX_RATE_LIMIT_WAIT_S = 30
CALL_TIMEOUT_S = 60   # Pro-class planning calls can take a while
RETRY_DELAY_S = 2


def has_api_key() -> bool:
    return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))


MISSING_KEY_HELP = ("GOOGLE_API_KEY is not set. Put it in .env.local in the project folder (GOOGLE_API_KEY=...) "
                    "or export it in your shell, then run again. Use --no-llm to run without any LLM calls.")


class LLMError(Exception):
    """The model could not produce valid output. Callers fall back instead of crashing."""


class LLM(Protocol):
    async def generate_json(self, *, model: str, system: str, prompt: str, schema: type[T]) -> T: ...


class GeminiLLM:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not has_api_key():
                raise LLMError("no GOOGLE_API_KEY / GEMINI_API_KEY set")
            from google import genai
            self._client = genai.Client()
        return self._client

    async def generate_json(self, *, model: str, system: str, prompt: str, schema: type[T]) -> T:
        from google.genai import errors, types

        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        )
        last: Exception | None = None
        for attempt in range(2):  # one retry: malformed/truncated JSON, or a temporary server error
            try:
                async with asyncio.timeout(CALL_TIMEOUT_S):
                    resp = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
                return schema.model_validate_json(resp.text or "")
            except errors.ClientError as e:
                if e.code in (400, 401, 403, 404) or (e.code == 429 and "limit: 0" in str(e)):
                    raise LLMError(f"{model}: {e.code} {e.status}") from e  # retrying won't help
                last = e
                if e.code == 429 and attempt == 0:
                    # Rate limit (not a zero quota): wait as long as the API asks, within reason.
                    m = re.search(r"retry(?:Delay| in)['\": ]+([\d.]+)s", str(e))
                    await asyncio.sleep(min(float(m.group(1)) if m else 10, MAX_RATE_LIMIT_WAIT_S))
                    continue
            except TimeoutError as e:
                # An overloaded model rarely recovers within seconds; let the caller try the next model.
                raise LLMError(f"{model}: no response within {CALL_TIMEOUT_S}s") from e
            except (ValidationError, ValueError, errors.APIError) as e:
                last = e
            if attempt == 0 and isinstance(last, errors.APIError):
                await asyncio.sleep(RETRY_DELAY_S)
        raise LLMError(f"{model}: {type(last).__name__}: {str(last)[:160]}")


# Models found unavailable in this run: model → time until which it's skipped. Saves waiting for the
# same quota error or timeout on every call (e.g. an overloaded model costs 60 s per call otherwise).
_unavailable: dict[str, float] = {}
SKIP_AFTER_TIMEOUT_S = 300


def _mark_unavailable(model: str, error: str) -> None:
    permanent = any(k in error for k in ("RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "403", "401"))
    _unavailable[model] = float("inf") if permanent else time.monotonic() + SKIP_AFTER_TIMEOUT_S


async def generate_json_with_fallback(llm: LLM, models: list[str], *, system: str, prompt: str,
                                      schema: type[T]) -> tuple[T, str, list[str]]:
    """Try each model in order. Returns (result, model used, notes about models that failed or were skipped)."""
    notes: list[str] = []
    candidates = list(dict.fromkeys(models))
    for i, model in enumerate(candidates):
        is_last = i == len(candidates) - 1
        if _unavailable.get(model, 0) > time.monotonic() and not is_last:
            notes.append(f"{model} skipped (unavailable earlier in this run)")
            continue
        try:
            return await llm.generate_json(model=model, system=system, prompt=prompt, schema=schema), model, notes
        except LLMError as e:
            if isinstance(llm, GeminiLLM):   # fake LLMs in tests don't poison the shared state
                _mark_unavailable(model, str(e))
            notes.append(f"{model} unavailable ({str(e)[:120]})")
    raise LLMError("; ".join(notes) or "no models configured")
