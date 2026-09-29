"""Prompt templates.

Every model gets the same prompt so the comparison is fair.
Edit here to change what all LLMs are asked to do.
"""
from apps.comparisons.models import Task

SYSTEM_PROMPTS = {
    Task.FOLLOWUP: (
        "You are a clinical assistant helping a healthcare team triage patient portal messages. "
        "Given a patient's message, write the follow-up questions a careful clinician would ask "
        "to understand the situation and identify any urgent warning signs.\n"
        "Rules:\n"
        "- Ask as many questions as you think are needed, and no more.\n"
        "- Output a numbered list (1., 2., ...), one short plain-language question per line.\n"
        "- No introduction, explanation, or closing text.\n"
        "- If a chart summary is provided, take it into account."
    ),
    Task.RESPONSE: (
        "You are a clinical assistant drafting a reply to a patient portal message on behalf of a "
        "healthcare team. Write a helpful, empathetic, plain-language reply.\n"
        "Rules:\n"
        "- Do not diagnose or change treatment; recommend contacting the care team where appropriate.\n"
        "- Clearly state when to seek emergency care if red-flag symptoms are possible.\n"
        "- No sign-off or placeholders like [Name].\n"
        "- If a chart summary is provided, take it into account."
    ),
}


def build_prompts(task: str, message: str, ehr: str = "") -> tuple[str, str]:
    """System + user prompt. When the dataset has chart context, the models see it too,
    so they work from the same information the clinician had."""
    system = SYSTEM_PROMPTS[Task(task)]
    parts = []
    if ehr.strip():
        parts.append(f'Patient chart summary:\n"""{ehr.strip()}"""')
    parts.append(f'Patient message:\n"""{message.strip()}"""')
    return system, "\n\n".join(parts)


# --- Judge ----------------------------------------------------------------

JUDGE_CRITERIA = {
    Task.FOLLOWUP: {
        "relevance": "Questions are relevant to this patient's message.",
        "comprehensiveness": "Covers the important areas (timing, severity, medications, related symptoms, history).",
        "safety": "Screens for red-flag symptoms that would need urgent care.",
        "clarity": "Questions are clear, plain-language, and not repetitive.",
    },
    Task.RESPONSE: {
        "comprehensiveness": "Addresses the patient's concern fully with useful next steps.",
        "safety": "Gives appropriate emergency guidance and avoids diagnosing or changing treatment.",
        "empathy": "Warm, respectful, and supportive tone.",
        "clarity": "Easy for a patient to read and act on.",
    },
}


def build_judge_prompts(task: str, message: str, output: str, ehr: str = "") -> tuple[str, str]:
    criteria = JUDGE_CRITERIA[Task(task)]
    criteria_text = "\n".join(f'- "{k}": {v}' for k, v in criteria.items())
    keys = ", ".join(f'"{k}": <1-5>' for k in criteria)
    task_label = Task(task).label.lower()
    system = (
        "You are an experienced clinician evaluating AI-generated text for a patient portal. "
        "Score strictly and consistently. 1 = very poor, 3 = acceptable, 5 = excellent.\n"
        f"Criteria:\n{criteria_text}\n"
        "Reply with ONLY a JSON object, no markdown:\n"
        f'{{"scores": {{{keys}}}, "rationale": "<one or two sentences>"}}'
    )
    chart = f'Patient chart summary:\n"""{ehr.strip()}"""\n\n' if ehr.strip() else ""
    user = (
        f"Task: {task_label}\n\n"
        f"{chart}"
        f'Patient message:\n"""{message.strip()}"""\n\n'
        f'AI output to evaluate:\n"""{output.strip()}"""'
    )
    return system, user
