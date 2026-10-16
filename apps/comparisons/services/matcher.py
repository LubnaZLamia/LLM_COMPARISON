"""Clinician matching by meaning, using the same chat API the app already calls.

The word-overlap baseline in `metrics.py` scores 0 when a model asks the
clinician's question in different words, which is the dominant error in this
dataset: "any blood in your stool?" and "have you noticed rectal bleeding?" share
no content words at all.

Rather than add an embedding service, this asks a small language model to do the
alignment, which is the modern form of the task — recognising textual entailment
between two short clinical statements. It goes through exactly the same
`chat()` client, key and endpoint that generate and judge every output, so there
is no new failure mode to discover on demo day.

The prompt is deliberately narrow. The model never scores quality and never sees
which system wrote the answer; it only says, for each clinician item, whether the
other text covers it. That keeps it closer to an annotator than a judge.

One call per output, in parallel, at temperature 0. Any failure falls back to the
word-overlap score, which always runs.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from django.conf import settings

from apps.llms.models import LLMModel
from apps.llms.services.openrouter import OpenRouterError, chat

SYSTEM = (
    "You align two pieces of clinical text. You are given a numbered list of items a clinician wrote, "
    "and a candidate text. For each clinician item, decide whether the candidate text covers it — that "
    "is, whether it asks for or states the same clinical information, even in completely different words.\n"
    "Count it as covered when the candidate seeks the same information, including when one broad "
    "statement covers the item among others.\n"
    "Do not count it when the candidate only mentions a related topic, when it covers the opposite "
    "(for example 'no need to come in' against 'come in today'), or when the clinical specifics differ.\n"
    "Judge only coverage. Never judge how good, safe or well written either text is.\n"
    'Reply with JSON only: {"covered": [list of the numbers that are covered]}. No other text.'
)


@dataclass
class MatchResult:
    score: float
    matched: int
    total: int
    missed: list[str] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    error: str = ""


def matcher_model_id() -> str:
    """The model that does the alignment: a small, cheap one is the right tool."""
    if settings.MATCH_MODEL:
        return settings.MATCH_MODEL
    model = (LLMModel.objects.filter(is_active=True, provider__iexact="anthropic").first()
             or LLMModel.objects.filter(is_judge=True).first()
             or LLMModel.objects.filter(is_active=True).first())
    return model.openrouter_id if model else ""


def build_prompt(reference_items: list[str], candidate: str, task: str) -> str:
    numbered = "\n".join(f"{i}. {item}" for i, item in enumerate(reference_items, 1))
    kind = "questions the clinician asked this patient" if task == "followup" else \
           "points the clinician made in their reply to this patient"
    return (f"The {kind}:\n{numbered}\n\n"
            f"The candidate text:\n\"\"\"\n{candidate.strip()}\n\"\"\"\n\n"
            f"Which of the {len(reference_items)} numbered items does the candidate text cover?")


def parse_covered(raw: str, total: int) -> set[int]:
    """Read {"covered": [1, 3]} out of the reply, tolerating stray prose around it."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("Matcher did not return JSON")
    data = json.loads(match.group(0))
    values = data.get("covered", data if isinstance(data, list) else [])
    if not isinstance(values, list):
        raise ValueError("Matcher returned no usable list")
    covered = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= total:
            covered.add(number)
    return covered


def match_one(model_id: str, reference_items: list[str], candidate: str, task: str) -> MatchResult:
    total = len(reference_items)
    if not total or not candidate.strip():
        return MatchResult(score=0.0, matched=0, total=total, missed=list(reference_items))
    try:
        # One attempt only: this enriches a metric that already has a working
        # fallback, so a slow retry here would delay the results page for nothing.
        result = chat(model_id, SYSTEM, build_prompt(reference_items, candidate, task),
                      temperature=0, max_attempts=1)
        covered = parse_covered(result.text, total)
    except (OpenRouterError, ValueError, json.JSONDecodeError) as exc:
        return MatchResult(score=0.0, matched=0, total=total, error=str(exc))
    hit = [item for i, item in enumerate(reference_items, 1) if i in covered]
    missed = [item for i, item in enumerate(reference_items, 1) if i not in covered]
    return MatchResult(score=round(len(hit) / total, 3), matched=len(hit), total=total,
                       missed=missed, covered=hit)


def match_many(reference_items: list[str], candidates: dict[int, str], task: str) -> dict[int, MatchResult]:
    """Align every model output against the clinician's answer, in parallel."""
    model_id = matcher_model_id()
    if not model_id or not reference_items or not candidates:
        return {}
    keys = list(candidates)
    with ThreadPoolExecutor(max_workers=min(4, len(keys))) as pool:
        results = pool.map(lambda key: match_one(model_id, reference_items, candidates[key], task), keys)
    return {key: result for key, result in zip(keys, results) if not result.error}


def available() -> bool:
    """True when the alignment call can be attempted at all."""
    return bool(settings.OPENROUTER_API_KEY) and not settings.LLM_MOCK_MODE and bool(matcher_model_id())
