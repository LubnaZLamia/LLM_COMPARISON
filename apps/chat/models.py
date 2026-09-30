"""Multi-turn chat. The conversation history lives here, in the database.

The model API remembers nothing between calls, so every new message is sent
together with the earlier ones (see services/chat.py: build_history).
"""
from django.db import models

from apps.llms.models import FALLBACK_COLOR, LLMModel, ink_of, line_of, mark_for, tint_of


class Conversation(models.Model):
    title = models.CharField(max_length=120, blank=True)
    llm = models.ForeignKey(
        LLMModel, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="The model picked last. Used as the default for the next message.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Chat #{self.pk}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Patient"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    error = models.TextField(blank=True)

    # Assistant replies only: which model answered, and how it went.
    llm = models.ForeignKey(LLMModel, null=True, blank=True, on_delete=models.SET_NULL)
    model_name = models.CharField(max_length=80, blank=True, help_text="Snapshot of the display name.")
    openrouter_id = models.CharField(max_length=160, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    truncated = models.BooleanField(default=False, help_text="Model hit the token limit before finishing.")
    stopped = models.BooleanField(default=False, help_text="The user stopped the reply before it finished.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_role_display()}: {self.content[:50]}"

    @property
    def is_user(self) -> bool:
        return self.role == self.Role.USER

    @property
    def latency_s(self) -> float | None:
        return self.latency_ms / 1000 if self.latency_ms is not None else None

    # Identity mark, kept working even if the LLMModel row has since been deleted.
    @property
    def color(self) -> str:
        return self.llm.color if self.llm else FALLBACK_COLOR

    @property
    def mark(self) -> str:
        return self.llm.mark if self.llm else mark_for("", self.model_name)

    @property
    def provider(self) -> str:
        return self.llm.provider if self.llm else self.openrouter_id.split("/")[0]

    @property
    def tint(self) -> str:
        return tint_of(self.color)

    @property
    def line(self) -> str:
        return line_of(self.color)

    @property
    def ink(self) -> str:
        return ink_of(self.color)
