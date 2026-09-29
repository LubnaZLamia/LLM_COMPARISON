"""Thin client for the OpenRouter chat completions API.

OpenRouter exposes an OpenAI-compatible API, so one key reaches every
provider. Docs: https://openrouter.ai/docs

Transient failures are retried. The common one is HTTP 429 ("Provider returned
error"): the provider OpenRouter routed the request to was momentarily rate
limited or out of capacity. It usually succeeds a second or two later, so a
single retry turns a red card into an answer.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import requests
from django.conf import settings


class OpenRouterError(Exception):
    pass


class RetryableError(OpenRouterError):
    """Temporary problem: rate limit, provider hiccup, timeout, empty reply."""


# Status codes worth trying again. 400/401/402/403/404 are not: a bad key,
# no credit or a retired model won't fix itself.
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504, 520, 522, 524}


@dataclass
class ChatResult:
    text: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str = ""
    attempts: int = 1

    @property
    def truncated(self) -> bool:
        """True when the model was cut off by the token limit instead of finishing."""
        return self.finish_reason == "length"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional attribution headers recommended by OpenRouter
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "LLM Health Assistant",
    }


def _retry_after(resp) -> float | None:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


def _once(model_id: str, system: str, user: str, temperature: float | None) -> ChatResult:
    """One attempt. Raises RetryableError for problems worth trying again."""
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": settings.LLM_MAX_TOKENS,
        # Let OpenRouter try another provider for this model when one is busy.
        "provider": {"allow_fallbacks": True},
    }
    start = time.perf_counter()
    try:
        resp = requests.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=settings.OPENROUTER_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise RetryableError(f"Timed out after {settings.OPENROUTER_TIMEOUT}s") from exc
    except requests.RequestException as exc:
        raise RetryableError(f"Network error: {exc}") from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    if resp.status_code in RETRY_STATUSES:
        wait = _retry_after(resp)
        detail = "rate limited or provider busy" if resp.status_code == 429 else "provider error"
        raise RetryableError(f"HTTP {resp.status_code}: {detail}" + (f" (retry after {wait:g}s)" if wait else ""))

    try:
        data = resp.json()
    except ValueError:
        raise RetryableError(f"HTTP {resp.status_code}: non-JSON response")

    if resp.status_code != 200 or "error" in data:
        err = data.get("error", {})
        msg = (err.get("message") if isinstance(err, dict) else str(err)) or "unknown error"
        if (isinstance(err, dict) and err.get("code") in RETRY_STATUSES) or resp.status_code in RETRY_STATUSES:
            raise RetryableError(f"HTTP {resp.status_code}: {msg}")
        raise OpenRouterError(f"HTTP {resp.status_code}: {msg}")

    try:
        choice = data["choices"][0]
        text = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RetryableError("Unexpected response format from OpenRouter")

    if not text:
        raise RetryableError("Model returned an empty reply")

    usage = data.get("usage") or {}
    return ChatResult(
        text=text,
        latency_ms=latency_ms,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        finish_reason=choice.get("finish_reason") or "",
    )


def chat(model_id: str, system: str, user: str, temperature: float | None = None) -> ChatResult:
    """Send one system+user prompt to `model_id`, retrying temporary failures."""
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not set. Add it to your .env file, or set LLM_MOCK_MODE=true.")

    attempts = max(1, settings.OPENROUTER_MAX_ATTEMPTS)
    last: RetryableError | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = _once(model_id, system, user, temperature)
            result.attempts = attempt
            return result
        except RetryableError as exc:
            last = exc
            if attempt == attempts:
                break
            # 1s, 2s, 4s ... plus jitter so parallel models don't retry in lockstep.
            time.sleep(min(settings.OPENROUTER_RETRY_BACKOFF * 2 ** (attempt - 1), 8) + random.uniform(0, 0.4))
    raise OpenRouterError(f"{last} (gave up after {attempts} attempts)")


def list_models() -> list[dict]:
    """Return OpenRouter's public model catalogue (no API key needed)."""
    resp = requests.get(f"{settings.OPENROUTER_BASE_URL}/models", timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def list_model_ids() -> set[str]:
    """Return every model id OpenRouter currently offers."""
    return {m["id"] for m in list_models()}
