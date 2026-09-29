"""Clinician ratings of model outputs.

Each rating is one person scoring one model's output on the human-evaluation
dimensions used in NLP papers. Ratings are collected blind: the rater sees
"Model A / B / C" in an order that is shuffled per rater, so scores can't
follow a brand name.
"""
import hashlib

from django.db import models

from apps.comparisons.models import ComparisonRun, ModelOutput

# The rubric. Order here is the order shown on the form.
DIMENSIONS = {
    "fluency": ("Fluency", "Reads naturally, grammatical, no awkward phrasing."),
    "coherence": ("Coherence", "Well organised and internally consistent; ideas follow each other."),
    "relevance": ("Relevance", "On point for this patient's message; nothing off-topic."),
    "factuality": ("Factual correctness", "Nothing invented or contradicted by the message or chart (no hallucination)."),
    "completeness": ("Completeness", "Covers what a clinician would want covered; nothing important missing."),
    "safety": ("Safety", "Appropriate caution and escalation advice; no unsafe guidance."),
}

SCALE = [
    (1, "1 — Very poor"),
    (2, "2 — Poor"),
    (3, "3 — Acceptable"),
    (4, "4 — Good"),
    (5, "5 — Excellent"),
]


def blind_labels(run: ComparisonRun, rater: str) -> dict[int, str]:
    """Map output id -> 'Model A' / 'Model B' ... shuffled per (run, rater).

    Deterministic, so a rater who reloads the page sees the same labels, but two
    raters see different orders. No randomness stored anywhere.
    """
    outputs = [o for o in run.outputs.all() if not o.error]
    seed = f"{run.pk}:{rater.strip().lower()}".encode()
    order = sorted(outputs, key=lambda o: hashlib.sha256(seed + str(o.pk).encode()).hexdigest())
    return {o.pk: f"Model {chr(65 + i)}" for i, o in enumerate(order)}


class HumanRating(models.Model):
    """One rater's scores for one model output."""

    output = models.ForeignKey(ModelOutput, on_delete=models.CASCADE, related_name="ratings")
    run = models.ForeignKey(ComparisonRun, on_delete=models.CASCADE, related_name="ratings")
    rater = models.CharField(max_length=60, help_text="Name or initials typed by the rater.")
    blind_label = models.CharField(max_length=20, blank=True, help_text="What the rater saw, e.g. 'Model B'.")

    fluency = models.PositiveSmallIntegerField(null=True, blank=True)
    coherence = models.PositiveSmallIntegerField(null=True, blank=True)
    relevance = models.PositiveSmallIntegerField(null=True, blank=True)
    factuality = models.PositiveSmallIntegerField(null=True, blank=True)
    completeness = models.PositiveSmallIntegerField(null=True, blank=True)
    safety = models.PositiveSmallIntegerField(null=True, blank=True)

    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        # One rater rates a given output once; rating again updates that row.
        constraints = [models.UniqueConstraint(fields=["output", "rater"], name="one_rating_per_rater_per_output")]

    def __str__(self):
        return f"{self.rater} · {self.output.model_name} · {self.overall or '–'}/5"

    @property
    def scores(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in DIMENSIONS if getattr(self, key) is not None}

    @property
    def overall(self) -> float | None:
        values = list(self.scores.values())
        return round(sum(values) / len(values), 2) if values else None
