"""Peer annotation: a non-expert says how similar a model output is to the clinician's answer.

The clinician Rate page asks for medical judgement (safety, factual correctness).
Nothing here does. The annotator only compares two texts:

1. For every item in the clinician's answer (one question, or one sentence of the
   reply): is it in the model's text, in any words?  Covered / Partly / Not covered
2. Overall, are the two texts similar?  Yes, with a rough percentage, or No, with reasons.
3. How much of the model's text has no counterpart in the clinician's answer?
4. How sure are you?

Step 1 is the human version of the automatic Clinician Match metric, item for item,
so the labels can check both matchers (word overlap and meaning).
"""
from django.db import models

from apps.comparisons.models import ModelOutput

ITEM_CHOICES = [("yes", "Covered"), ("partly", "Partly"), ("no", "Not covered")]
ITEM_WEIGHT = {"yes": 1.0, "partly": 0.5, "no": 0.0}


class Coverage(models.IntegerChoices):
    """How much of the clinician's answer B covers, when the annotator says the texts are similar.
    The value is the lower bound of the band, in percent."""
    SOME = 0, "Some of it (up to 25%)"
    HALF = 25, "About half (25–50%)"
    MOST = 50, "Most of it (50–75%)"
    NEARLY_ALL = 75, "Nearly all of it (75–100%)"


# Why the texts are not similar. Order here is the order on the form.
REASONS = {
    "different_topics": ("Different topics", "B seems to focus on other things, such as other symptoms, questions or advice."),
    "misses_most": ("Misses most of A", "B covers only a small part of A."),
    "too_general": ("Too general", "B is more general, while A is more specific."),
    "contradicts": ("Says the opposite", "B points the other way, for example 'no need to come in' where A says 'please come in'."),
    "wrong_kind": ("A different kind of text", "B is a different kind of answer, such as a reply instead of questions, or no real answer."),
    "other": ("Other", "Something else. Feel free to describe it in the box."),
}


class Extra(models.TextChoices):
    NONE = "none", "None: everything in B is also in A"
    LITTLE = "little", "A little (up to 25% of B)"
    SOME = "some", "Some (25–50% of B)"
    MOST = "most", "Most of B (over 50%)"


class Confidence(models.TextChoices):
    SURE = "sure", "Sure"
    FAIRLY = "fairly", "Fairly sure"
    UNSURE = "unsure", "Not sure"


class PeerAnnotation(models.Model):
    """One annotator's comparison of one model output with the clinician's answer."""

    output = models.ForeignKey(ModelOutput, on_delete=models.CASCADE, related_name="peer_annotations")
    annotator = models.CharField(max_length=60, help_text="Name or initials typed by the annotator.")

    # Step 1: the clinician's items as shown, and one label per item, in the same order.
    reference_items = models.JSONField(default=list)
    item_labels = models.JSONField(default=list, help_text="'yes' / 'partly' / 'no' for each reference item.")
    item_score = models.FloatField(null=True, blank=True,
                                   help_text="Share of clinician items covered (partly counts half), 0-1.")

    # Step 2: overall similarity.
    similar = models.BooleanField(null=True)
    coverage = models.PositiveSmallIntegerField(choices=Coverage.choices, null=True, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    other_reason = models.CharField(max_length=200, blank=True)

    # Steps 3 and 4.
    extra = models.CharField(max_length=10, choices=Extra.choices, blank=True)
    confidence = models.CharField(max_length=10, choices=Confidence.choices, blank=True)
    comment = models.TextField(blank=True)
    seconds = models.PositiveIntegerField(null=True, blank=True, help_text="Time spent on the item page.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        # One annotator annotates a given output once; annotating again updates that row.
        constraints = [models.UniqueConstraint(fields=["output", "annotator"], name="one_peer_annotation_per_output")]

    def __str__(self):
        return f"{self.annotator} · output {self.output_id} · {self.item_score if self.item_score is not None else '–'}"

    @staticmethod
    def score_items(labels: list[str]) -> float | None:
        weights = [ITEM_WEIGHT[label] for label in labels if label in ITEM_WEIGHT]
        return round(sum(weights) / len(weights), 3) if weights else None

    @property
    def overall(self) -> float | None:
        """The overall answer as one number, 0-1: No is 0, Yes is the middle of the chosen band."""
        if self.similar is None:
            return None
        if not self.similar:
            return 0.0
        return None if self.coverage is None else (self.coverage + 12.5) / 100

    @property
    def counts(self) -> dict[str, int]:
        return {key: self.item_labels.count(key) for key, _ in ITEM_CHOICES}

    @property
    def reason_labels(self) -> list[str]:
        return [REASONS[r][0] for r in self.reasons if r in REASONS]
