"""Runs one comparison: calls every selected model in parallel, analyses and stores the results."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

from apps.comparisons.models import ComparisonRun, ModelOutput, Task
from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, OpenRouterError, chat

from . import analysis, judge, metrics
from .prompts import build_prompts

_MOCK_QUESTIONS = [
    "When did this start?",
    "Which medication are you taking, and what dose?",
    "How severe is it on a scale of 0 to 10?",
    "Have you had chest pain, fainting, or trouble breathing?",
    "Are you taking any other medications or supplements?",
    "Have you had this problem before?",
    "Does it affect your daily activities or sleep?",
    "When did the symptoms begin?",
]


def _mock_reply(index: int, llm: LLMModel, task: str) -> ChatResult:
    if task == Task.FOLLOWUP:
        qs = _MOCK_QUESTIONS[index % 3: index % 3 + 4 + index]
        text = "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1))
    else:
        text = (
            "Thank you for your message. I'm sorry you're dealing with this. "
            "Please let us know when it started and any other symptoms so your care team can review it. "
        ) + ("Please don't stop your medication before talking with your provider. " * (index % 2)) + (
            "If you have chest pain, trouble breathing, or fainting, call 911 or go to the emergency room right away."
            if index != 1 else ""
        )
    return ChatResult(text=text, latency_ms=0)


def _call(index: int, llm: LLMModel, task: str, message: str):
    try:
        if settings.LLM_MOCK_MODE:
            return llm, _mock_reply(index, llm, task), ""
        system, user = build_prompts(task, message)
        return llm, chat(llm.openrouter_id, system, user), ""
    except OpenRouterError as exc:
        return llm, None, str(exc)
    except Exception as exc:  # never let one model crash the whole run
        return llm, None, f"Unexpected error: {exc}"


def run_comparison(*, message_text: str, task: str, llms: list[LLMModel], category: str,
                   source_message=None, use_judge: bool = False) -> ComparisonRun:
    reference = source_message.reference_for(task) if source_message else ""
    judge_id = judge.judge_model_id() if use_judge else ""
    run = ComparisonRun.objects.create(
        source_message=source_message, message_text=message_text, category=category, task=task,
        reference_text=reference, mock_mode=settings.LLM_MOCK_MODE, judge_model=judge_id,
    )
    workers = max(1, len(llms))

    # 1) Generate
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda pair: _call(pair[0], pair[1], task, message_text), enumerate(llms)))

    outputs = []
    for llm, result, error in results:
        out = ModelOutput(run=run, llm=llm, model_name=llm.display_name, openrouter_id=llm.openrouter_id, error=error)
        if result:
            out.text = result.text
            out.latency_ms = result.latency_ms
            out.prompt_tokens = result.prompt_tokens
            out.completion_tokens = result.completion_tokens
            m = metrics.score(result.text, reference)
            if m:
                out.reference_coverage = m.reference_coverage
                out.missing_key_info = m.missing_key_info
                out.relative_length = m.relative_length
                out.missed_points = m.missed_points
        outputs.append(out)

    # 2) Output-based analysis (needs all outputs together for agreement)
    ok = {i: o.text for i, o in enumerate(outputs) if not o.error and o.text}
    for i, data in analysis.analyze(task, ok).items():
        outputs[i].analysis = data

    # 3) Optional judge scores
    if judge_id and ok:
        if settings.LLM_MOCK_MODE:
            for i in ok:
                outputs[i].judge = judge.mock_scores(task, i)
        elif judge_id:
            with ThreadPoolExecutor(max_workers=len(ok)) as pool:
                scored = pool.map(lambda i: (i, judge.score_output(judge_id, task, message_text, ok[i])), ok)
                for i, data in scored:
                    outputs[i].judge = data

    for out in outputs:
        out.save()
    return run
