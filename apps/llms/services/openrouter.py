"""Thin client for the OpenRouter chat completions API.

OpenRouter exposes an OpenAI-compatible API, so one key reaches every
provider. Docs: https://openrouter.ai/docs
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from django.conf import settings


class OpenRouterError(Exception):
    pass


@dataclass
class ChatResult:
    text: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional attribution headers recommended by OpenRouter
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "LLM Health Assistant",
    }


def chat(model_id: str, system: str, user: str, temperature: float | None = None) -> ChatResult:
    """Send one system+user prompt to `model_id` and return the reply text."""
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not set. Add it to your .env file, or set LLM_MOCK_MODE=true.")

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": settings.LLM_MAX_TOKENS,
    }
    start = time.perf_counter()
    try:
        resp = requests.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=settings.OPENROUTER_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise OpenRouterError(f"Network error: {exc}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    try:
        data = resp.json()
    except ValueError:
        raise OpenRouterError(f"HTTP {resp.status_code}: non-JSON response")

    if resp.status_code != 200 or "error" in data:
        err = data.get("error", {})
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise OpenRouterError(f"HTTP {resp.status_code}: {msg or 'unknown error'}")

    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise OpenRouterError("Unexpected response format from OpenRouter")

    usage = data.get("usage") or {}
    return ChatResult(
        text=text.strip(),
        latency_ms=latency_ms,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


def list_models() -> list[dict]:
    """Return OpenRouter's public model catalogue (no API key needed)."""
    resp = requests.get(f"{settings.OPENROUTER_BASE_URL}/models", timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def list_model_ids() -> set[str]:
    """Return every model id OpenRouter currently offers."""
    return {m["id"] for m in list_models()}
