"""Peer annotation: a non-expert says how similar a model output is to the clinician's answer.

The clinician Rate page asks for medical judgement (safety, factual correctness).
Nothing here does. The annotator only compares two texts:

1. For every item in the clinician's answer (one question, or one sentence of the
   reply): is it in the model's text, in any words?  Covered / Partly / Not covered.
   For Covered and Partly, which part of the model's text says it.
2. Overall, are the two texts similar?  Yes, with a rough percentage, or No, with reasons.
3. How sure are you?

Step 1 is the human version of the automatic Clinician Match metric, item for item,
so the labels can check both matchers (word overlap and meaning). The links in step 1
give the other direction too: how much of the model's text the clinician also said
(precision, where the item score is recall).
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
    """How much of B matches nothing in A. Worked out from the links in step 1."""
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

    # The model's text in numbered parts as shown, and for each clinician item the parts
    # of B that say it (0-based indexes into candidate_units; empty for Not covered).
    candidate_units = models.JSONField(default=list)
    item_links = models.JSONField(default=list)
    unit_score = models.FloatField(null=True, blank=True,
                                   help_text="Share of B's parts that some clinician item points to, 0-1.")

    # Step 2: overall similarity.
    similar = models.BooleanField(null=True)
    coverage = models.PositiveSmallIntegerField(choices=Coverage.choices, null=True, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    other_reason = models.CharField(max_length=200, blank=True)

    extra = models.CharField(max_length=10, choices=Extra.choices, blank=True)

    # Step 3.
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

    @staticmethod
    def score_units(links: list[list[int]], total: int) -> float | None:
        linked = {j for indexes in links for j in indexes}
        return round(len(linked) / total, 3) if total else None

    @staticmethod
    def extra_band(unit_score: float | None) -> str:
        if unit_score is None:
            return ""
        unmatched = 1 - unit_score
        if unmatched == 0:
            return Extra.NONE
        return Extra.LITTLE if unmatched <= .25 else Extra.SOME if unmatched <= .5 else Extra.MOST

    @property
    def f1(self) -> float | None:
        """Harmonic mean of the item score (recall) and the unit score (precision)."""
        r, p = self.item_score, self.unit_score
        if r is None or p is None:
            return None
        return 0.0 if r + p == 0 else round(2 * r * p / (r + p), 3)

    @property
    def units_matched(self) -> int:
        return len({j for links in self.item_links for j in links})

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
