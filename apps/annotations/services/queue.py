"""Which outputs can be annotated, and which one an annotator gets next."""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.db.models import Count

from apps.comparisons.models import ModelOutput, Task
from apps.comparisons.services.analysis import extract_questions, reference_items


def eligible(task: str = ""):
    """Model outputs that have a clinician answer to compare with.

    Left out: failed outputs, empty ones, runs without ground truth, and mock-mode
    runs (their outputs are canned text, so comparing them tells us nothing).
    """
    qs = (ModelOutput.objects.filter(error="", run__mock_mode=False)
          .exclude(text="").exclude(run__reference_text="")
          .select_related("run", "llm"))
    if task in Task.values:
        qs = qs.filter(run__task=task)
    return qs


def items_for(output: ModelOutput) -> list[str]:
    """The clinician's answer split into items: one question per line, or one sentence each."""
    return reference_items(output.run.task, output.run.reference_text)


def candidate_items(output: ModelOutput) -> list[str]:
    """The model's follow-up questions as a list, so the two sides look alike. Empty for replies."""
    return extract_questions(output.text) if output.run.task == Task.FOLLOWUP else []


def _shuffle_key(annotator: str, pk: int) -> str:
    # Stable per annotator, different between annotators: no stored randomness.
    return hashlib.sha256(f"{annotator.strip().lower()}:{pk}".encode()).hexdigest()


def next_for(annotator: str, task: str = "", skip: set[int] | None = None) -> ModelOutput | None:
    """The next output this annotator hasn't done.

    Outputs below the target number of annotators come first, and among those the ones
    that already have an annotation, so items reach two annotators (and can be used for
    agreement) before new ones are opened. Ties are broken by a per-annotator shuffle.
    """
    target = max(1, settings.ANNOTATION_TARGET)
    rows = list(
        eligible(task)
        .exclude(peer_annotations__annotator__iexact=annotator)
        .annotate(n=Count("peer_annotations", distinct=True))
        .values_list("pk", "n")
    )
    if skip:
        kept = [r for r in rows if r[0] not in skip]
        rows = kept or rows   # once only skipped items remain, offer them again
    if not rows:
        return None
    pk, _ = min(rows, key=lambda r: (r[1] >= target, -r[1], _shuffle_key(annotator, r[0])))
    return eligible().get(pk=pk)


def progress(annotator: str, task: str = "") -> dict:
    qs = eligible(task)
    total = qs.count()
    mine = qs.filter(peer_annotations__annotator__iexact=annotator).distinct().count() if annotator else 0
    target = max(1, settings.ANNOTATION_TARGET)
    counted = qs.annotate(n=Count("peer_annotations", distinct=True))
    return {
        "total": total,
        "mine": mine,
        "left": total - mine,
        "pct": round(100 * mine / total) if total else 0,
        "at_target": counted.filter(n__gte=target).count(),
        "started": counted.filter(n__gte=1).count(),
        "target": target,
    }


def task_counts() -> list[dict]:
    """How many annotatable outputs each task has, for the task filter."""
    rows = []
    for value, label in Task.choices:
        rows.append({"value": value, "label": label, "count": eligible(value).count()})
    return rows

