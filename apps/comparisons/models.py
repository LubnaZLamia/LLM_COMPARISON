from django.db import models

from apps.dataset.models import Category, PatientMessage
from apps.llms.models import FALLBACK_COLOR, LLMModel, ink_of, line_of, mark_for, tint_of


class Task(models.TextChoices):
    FOLLOWUP = "followup", "Generate Follow-up Questions"
    RESPONSE = "response", "Generate General Response"


class ComparisonRun(models.Model):
    """One click of 'Generate Outputs': a message + task sent to several LLMs."""

    source_message = models.ForeignKey(
        PatientMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs",
        help_text="Set when the message came from the dataset.",
    )
    message_text = models.TextField()
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.OTHER)
    task = models.CharField(max_length=16, choices=Task.choices)
    ehr_summary = models.TextField(blank=True, help_text="Chart context sent to the models with the message.")
    reference_text = models.TextField(blank=True, help_text="Snapshot of the clinician ground truth used for metrics.")
    mock_mode = models.BooleanField(default=False)
    judge_model = models.CharField(max_length=160, blank=True, help_text="Judge model id, if scoring was requested.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run #{self.pk} · {self.get_task_display()}"

    @property
    def has_reference(self) -> bool:
        return bool(self.reference_text.strip())


class ModelOutput(models.Model):
    run = models.ForeignKey(ComparisonRun, on_delete=models.CASCADE, related_name="outputs")
    llm = models.ForeignKey(LLMModel, null=True, on_delete=models.SET_NULL)
    model_name = models.CharField(max_length=80, help_text="Snapshot of the display name.")
    openrouter_id = models.CharField(max_length=160)
    text = models.TextField(blank=True)
    error = models.TextField(blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=1, help_text="How many tries this answer needed.")
    truncated = models.BooleanField(default=False, help_text="Model hit the token limit before finishing.")
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)

    # Output-based analysis (no ground truth needed). See services/analysis.py
    analysis = models.JSONField(default=dict, blank=True)
    # LLM-as-judge scores: {"scores": {...}, "overall": float, "rationale": str} or {"error": str}
    judge = models.JSONField(default=dict, blank=True)

    # Reference-based metrics (null when no reference is available)
    reference_coverage = models.FloatField(null=True, blank=True)
    missing_key_info = models.FloatField(null=True, blank=True)
    relative_length = models.FloatField(null=True, blank=True)
    missed_points = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["run", "llm__order", "id"]

    def __str__(self):
        return f"{self.model_name} · run {self.run_id}"

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def looks_temporary(self) -> bool:
        """True when the error is the kind that usually works on a retry."""
        e = self.error.lower()
        return any(k in e for k in ("429", "rate limit", "busy", "timed out", "timeout", "network",
                                    "empty reply", "500", "502", "503", "504"))

    @property
    def color(self) -> str:
        return self.llm.color if self.llm else FALLBACK_COLOR

    # Identity mark, kept working even if the LLMModel row has since been deleted.
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
