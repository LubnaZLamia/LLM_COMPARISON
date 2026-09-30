"""Builds the history for each call and streams the model's reply into the database.

Memory works like this: every message is saved in the database, and each new
call sends the system prompt plus the most recent CHAT_HISTORY_MESSAGES messages.
Failed replies are left out, so the model never sees an error as something it said.
"""
from __future__ import annotations

import time

from django.conf import settings
from django.utils import timezone

from apps.chat.models import ChatMessage, Conversation
from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, ChatStream, OpenRouterError

from .prompts import SYSTEM_PROMPT

MAX_MESSAGE_CHARS = 8000
TITLE_CHARS = 60
# Pause between words in mock mode, so the streaming UI can be seen working. Tests set it to 0.
MOCK_WORD_DELAY = 0.03
SAVED_FIELDS = ("content", "error", "latency_ms", "prompt_tokens", "completion_tokens", "truncated", "stopped")


def title_from(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"


def build_history(conversation: Conversation) -> list[dict]:
    """System prompt + the recent turns, oldest first, in OpenAI message format."""
    turns = [m for m in conversation.messages.all() if m.content.strip() and not m.error]
    limit = max(1, settings.CHAT_HISTORY_MESSAGES)
    turns = turns[-limit:]
    # Cutting the history can leave an assistant reply at the front; start on a patient message.
    while turns and turns[0].role != ChatMessage.Role.USER:
        turns.pop(0)
    return [{"role": "system", "content": SYSTEM_PROMPT}] + [{"role": m.role, "content": m.content} for m in turns]


class MockStream:
    """Stands in for ChatStream when LLM_MOCK_MODE is on: no API call, no cost."""

    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.result: ChatResult | None = None

    def __iter__(self):
        last = next((m["content"] for m in reversed(self.messages) if m["role"] == "user"), "")
        turns = sum(1 for m in self.messages if m["role"] == "user")
        text = (
            f"**Mock reply** (turn {turns}) — no model was called.\n\n"
            f"You said: \"{title_from(last)}\"\n\n"
            "A few questions that would help:\n"
            "1. When did this start?\n"
            "2. How bad is it on a scale of 0 to 10?\n"
            "3. Are you taking any new medications?\n\n"
            "If you have chest pain, trouble breathing or fainting, call 911 right away."
        )
        words = text.split(" ")
        for i, word in enumerate(words):
            if MOCK_WORD_DELAY:
                time.sleep(MOCK_WORD_DELAY)
            yield word if i == len(words) - 1 else word + " "
        self.result = ChatResult(text=text, latency_ms=0)


def stream_reply(conversation: Conversation, llm: LLMModel):
    """Ask `llm` for the next reply. Yields ("delta", {...}) while text arrives,
    then one ("done", {...}) or ("error", {...}).

    The reply is saved even when the browser disconnects mid-stream (Stop button,
    closed tab): the web server closes this generator, and `finally` still runs.
    """
    # Build the history first, then save an empty reply straight away: it keeps its
    # place in the thread even if the patient stops it and sends a new message.
    messages = build_history(conversation)
    reply = ChatMessage.objects.create(
        conversation=conversation, role=ChatMessage.Role.ASSISTANT,
        llm=llm, model_name=llm.display_name, openrouter_id=llm.openrouter_id,
    )
    parts: list[str] = []
    finished = False
    try:
        stream = MockStream(messages) if settings.LLM_MOCK_MODE else ChatStream(llm.openrouter_id, messages)
        for piece in stream:
            parts.append(piece)
            yield "delta", {"text": piece}
        result = stream.result
        reply.content = result.text
        reply.latency_ms = result.latency_ms
        reply.prompt_tokens = result.prompt_tokens
        reply.completion_tokens = result.completion_tokens
        reply.truncated = result.truncated
        finished = True
    except OpenRouterError as exc:
        reply.error = str(exc)
    except Exception as exc:  # never leave the chat hanging on a bug
        reply.error = f"Unexpected error: {exc}"
    finally:
        if not finished and not reply.error:
            reply.stopped = True
        if not reply.content:
            reply.content = "".join(parts).strip()
        if reply.stopped and not reply.content:
            reply.error = "Stopped before the model replied."
        # update(), not save(): if the reply or chat was deleted meanwhile (Regenerate right
        # after Stop, or Delete), this does nothing instead of bringing the row back.
        ChatMessage.objects.filter(pk=reply.pk).update(**{f: getattr(reply, f) for f in SAVED_FIELDS})
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())

    if reply.error:
        yield "error", {"id": reply.pk, "error": reply.error}
    else:
        yield "done", message_meta(reply)


def message_meta(m: ChatMessage) -> dict:
    return {
        "id": m.pk,
        "model_name": m.model_name,
        "latency_ms": m.latency_ms,
        "prompt_tokens": m.prompt_tokens,
        "completion_tokens": m.completion_tokens,
        "truncated": m.truncated,
    }
