"""Optional LLM-as-judge scoring.

A separate model scores each output 1-5 on task-specific criteria
(see JUDGE_CRITERIA in prompts.py). Costs one extra API call per output.

Note: a model may favour its own style, so if the judge is also one of the
compared models, keep that in mind when reading its scores.
"""
from __future__ import annotations

import json
import re

from django.conf import settings

from apps.llms.models import LLMModel
from apps.llms.services.openrouter import OpenRouterError, chat

from .prompts import JUDGE_CRITERIA, build_judge_prompts


def judge_model_id() -> str:
    if settings.JUDGE_MODEL:
        return settings.JUDGE_MODEL
    m = LLMModel.objects.filter(is_judge=True).first() or LLMModel.objects.filter(is_active=True).first()
    return m.openrouter_id if m else ""


def parse_scores(raw: str, task: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("Judge did not return JSON")
    data = json.loads(match.group(0))
    scores_in = data.get("scores", data)
    scores = {}
    for key in JUDGE_CRITERIA[task]:
        try:
            scores[key] = min(5, max(1, int(round(float(scores_in[key])))))
        except (KeyError, TypeError, ValueError):
            continue
    if not scores:
        raise ValueError("Judge returned no usable scores")
    return {
        "scores": scores,
        "overall": round(sum(scores.values()) / len(scores), 2),
        "rationale": str(data.get("rationale", "")).strip(),
    }


def mock_scores(task: str, index: int) -> dict:
    scores = {k: 3 + (i + index) % 3 for i, k in enumerate(JUDGE_CRITERIA[task])}
    return {"scores": scores, "overall": round(sum(scores.values()) / len(scores), 2),
            "rationale": "Mock judge score (mock mode is on)."}


def score_output(model_id: str, task: str, message: str, output: str, ehr: str = "") -> dict:
    try:
        system, user = build_judge_prompts(task, message, output, ehr)
        result = chat(model_id, system, user, temperature=0)
        return parse_scores(result.text, task)
    except (OpenRouterError, ValueError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}
