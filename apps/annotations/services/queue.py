"""Which outputs can be annotated, and which one an annotator gets next."""
from __future__ import annotations

import hashlib
import re

from django.conf import settings
from django.db.models import Count

from apps.comparisons.models import ModelOutput, Task
from apps.comparisons.services.analysis import extract_questions, reference_items
from apps.comparisons.services.metrics import content_tokens

_LIST_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Short openers and sign-offs ("Hi Margaret,", "Best regards,", "[Your Name]") are not content.
_PLEASANTRY = re.compile(r"^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|best|kind regards|warm regards"
                         r"|regards|warmly|sincerely|thanks|thank you|take care|cheers)\b|^\[.*\]$", re.IGNORECASE)


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


def _is_unit(text: str) -> bool:
    if not content_tokens(text):
        return False
    short = len(text.split()) <= 5
    return not (short and (_PLEASANTRY.match(text) or text.endswith(":")))


def segments(output: ModelOutput) -> list[list[tuple[int | None, str]]]:
    """The model's text as paragraphs of (number, text) parts, for display and linking.

    Follow-ups are one question per part. Replies are one sentence per part, with the
    paragraphs kept so the reply still reads as written. Greetings, sign-offs and
    headings stay on screen but get no number: nobody links to "Hi Margaret,".
    """
    if output.run.task == Task.FOLLOWUP:
        questions = extract_questions(output.text)
        if questions:
            return [[(i, q)] for i, q in enumerate(questions)]
    paragraphs, n = [], 0
    for line in output.text.replace("**", "").splitlines():
        line = _LIST_PREFIX.sub("", line).strip()
        if not line:
            continue
        parts = []
        for sentence in _SENTENCE_END.split(line):
            if _is_unit(sentence):
                parts.append((n, sentence))
                n += 1
            else:
                parts.append((None, sentence))
        paragraphs.append(parts)
    return paragraphs


def candidate_units(output: ModelOutput) -> list[str]:
    """The numbered parts of the model's text, in order."""
    return [text for paragraph in segments(output) for number, text in paragraph if number is not None]


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

