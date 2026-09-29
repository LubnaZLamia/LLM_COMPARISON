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


def _call(index: int, llm: LLMModel, task: str, message: str, ehr: str = ""):
    try:
        if settings.LLM_MOCK_MODE:
            return llm, _mock_reply(index, llm, task), ""
        system, user = build_prompts(task, message, ehr)
        return llm, chat(llm.openrouter_id, system, user), ""
    except OpenRouterError as exc:
        return llm, None, str(exc)
    except Exception as exc:  # never let one model crash the whole run
        return llm, None, f"Unexpected error: {exc}"


def generate(run: ComparisonRun, llms: list[LLMModel]) -> None:
    """Call each model in parallel and save one ModelOutput per model."""
    with ThreadPoolExecutor(max_workers=max(1, len(llms))) as pool:
        results = list(pool.map(lambda pair: _call(pair[0], pair[1], run.task, run.message_text, run.ehr_summary), enumerate(llms)))

    for llm, result, error in results:
        out = ModelOutput(run=run, llm=llm, model_name=llm.display_name, openrouter_id=llm.openrouter_id, error=error)
        if result:
            out.text = result.text
            out.latency_ms = result.latency_ms
            out.prompt_tokens = result.prompt_tokens
            out.completion_tokens = result.completion_tokens
            out.attempts = result.attempts
            out.truncated = result.truncated
            m = metrics.score(result.text, run.reference_text)
            if m:
                out.reference_coverage = m.reference_coverage
                out.missing_key_info = m.missing_key_info
                out.relative_length = m.relative_length
                out.missed_points = m.missed_points
        out.save()


def analyse(run: ComparisonRun) -> None:
    """(Re)compute the output-based analysis. Needs all outputs together for agreement."""
    outputs = list(run.outputs.all())
    ok = {i: o.text for i, o in enumerate(outputs) if not o.error and o.text}
    results = analysis.analyze(run.task, ok, run.reference_text)
    for i, out in enumerate(outputs):
        data = results.get(i, {})
        if out.analysis != data:
            out.analysis = data
            out.save(update_fields=["analysis"])


def score_with_judge(run: ComparisonRun) -> None:
    """Score any answered output that has no judge score yet."""
    if not run.judge_model:
        return
    pending = [o for o in run.outputs.all() if not o.error and o.text and not o.judge]
    if not pending:
        return
    if settings.LLM_MOCK_MODE:
        for i, out in enumerate(pending):
            out.judge = judge.mock_scores(run.task, i)
            out.save(update_fields=["judge"])
        return
    with ThreadPoolExecutor(max_workers=len(pending)) as pool:
        scored = pool.map(
            lambda o: (o, judge.score_output(run.judge_model, run.task, run.message_text, o.text, run.ehr_summary)), pending
        )
        for out, data in scored:
            out.judge = data
            out.save(update_fields=["judge"])


def run_comparison(*, message_text: str, task: str, llms: list[LLMModel], category: str,
                   source_message=None, use_judge: bool = False) -> ComparisonRun:
    run = ComparisonRun.objects.create(
        source_message=source_message,
        message_text=message_text,
        category=category,
        task=task,
        ehr_summary=source_message.ehr_summary if source_message else "",
        reference_text=source_message.reference_for(task) if source_message else "",
        mock_mode=settings.LLM_MOCK_MODE,
        judge_model=judge.judge_model_id() if use_judge else "",
    )
    generate(run, llms)
    analyse(run)
    score_with_judge(run)
    return run


def retry_failed(run: ComparisonRun) -> list[str]:
    """Re-run only the models that failed in this run. Returns their names."""
    failed = [o for o in run.outputs.all() if o.error and o.llm]
    if not failed:
        return []
    llms = [o.llm for o in failed]
    names = [o.model_name for o in failed]
    ModelOutput.objects.filter(pk__in=[o.pk for o in failed]).delete()
    generate(run, llms)
    analyse(run)
    score_with_judge(run)
    return names
