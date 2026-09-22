"""Turns stored analysis into what the results page shows: highlight tiles, metric panels, detail rows."""
from __future__ import annotations

from django.conf import settings

from apps.comparisons.models import Task

from .analysis import ELEMENTS, TOPICS
from .prompts import JUDGE_CRITERIA

N_TOPICS = len(TOPICS)
N_ELEMENTS = len(ELEMENTS)


def _panel(outputs, *, label, help, value, display, direction=None, scale=None, detail=None):
    """Build one metric panel.

    value(out) -> number or None; display(v) -> str; direction: 'higher' | 'lower' | None;
    scale: fixed max for bar length (defaults to the largest value).
    """
    rows = []
    for out in outputs:
        v = value(out)
        if v is None:
            continue
        rows.append({"name": out.model_name, "color": out.color, "raw": v, "value": display(v)})
    if not rows:
        return None
    top = scale or max(r["raw"] for r in rows) or 1
    for r in rows:
        r["pct"] = round(min(r["raw"] / top, 1) * 100)
    if direction and len(rows) > 1:
        best = (max if direction == "higher" else min)(r["raw"] for r in rows)
        if any(r["raw"] != best for r in rows):  # no marker when everyone ties
            for r in rows:
                r["best"] = r["raw"] == best
    arrow = {"higher": "↑", "lower": "↓"}.get(direction, "")
    return {"label": label, "arrow": arrow, "help": help, "rows": rows}


def _a(key):
    return lambda o: o.analysis.get(key) if o.analysis else None


def _len_of(key):
    return lambda o: len(o.analysis[key]) if o.analysis and key in o.analysis else None


def _winner(outputs, value, pick=max, fmt=str):
    """Return (names, formatted value) of the best model(s) for a highlight tile."""
    vals = [(o, value(o)) for o in outputs]
    vals = [(o, v) for o, v in vals if v is not None]
    if len(vals) < 2:
        return None
    best = pick(v for _, v in vals)
    winners = [o for o, v in vals if v == best]
    names = "All models tied" if len(winners) == len(vals) else " & ".join(o.model_name for o in winners)
    color = winners[0].color if len(winners) < len(vals) else "#64748b"
    return {"names": names, "value": fmt(best), "color": color}


def build(run) -> dict:
    outputs = list(run.outputs.all())
    ok = [o for o in outputs if not o.error]
    panels, highlights = [], []

    # Three headline metrics per task. Everything else (unique / repeated questions,
    # topic lists, per-criterion judge scores) lives in the table view.
    if run.task == Task.FOLLOWUP:
        highlights += [
            ("Asked the most", _winner(ok, _a("question_count"), fmt=lambda v: f"{v} questions")),
            ("Covered the most ground", _winner(ok, _len_of("topics"), fmt=lambda v: f"{v} of {N_TOPICS} topics")),
            ("Most in agreement", _winner(ok, _a("agreement"), fmt=lambda v: f"{v:.0%} shared")),
        ]
        panels += [
            _panel(ok, label="Questions Asked", value=_a("question_count"), display=str,
                   help="How many follow-up questions the model asked. More isn't always better."),
            _panel(ok, label="Topics Covered", direction="higher", scale=N_TOPICS, value=_len_of("topics"),
                   display=lambda v: f"{v}/{N_TOPICS}",
                   help="How many of 8 clinical areas the questions touch: timing, severity, description, medications, other symptoms, red flags, history, daily impact."),
            _panel(ok, label="Agreement with Other Models", direction="higher", scale=1, value=_a("agreement"),
                   display=lambda v: f"{v:.0%}",
                   help="Share of this model's questions that another model also asked. High means it stuck to the obvious questions; low means it went its own way."),
        ]
    else:
        highlights += [
            ("Longest reply", _winner(ok, _a("word_count"), fmt=lambda v: f"{v} words")),
            ("Easiest to read", _winner(ok, _a("grade_level"), pick=min, fmt=lambda v: f"grade {v}")),
            ("Most complete", _winner(ok, _len_of("elements"), fmt=lambda v: f"{v} of {N_ELEMENTS} elements")),
        ]
        panels += [
            _panel(ok, label="Response Length (words)", value=_a("word_count"), display=str,
                   help="Word count of the reply."),
            _panel(ok, label="Reading Grade Level", direction="lower", scale=16, value=_a("grade_level"),
                   display=lambda v: f"{v}",
                   help="US school grade needed to read the reply (Flesch-Kincaid). 8 or below is patient-friendly."),
            _panel(ok, label="Key Elements", direction="higher", scale=N_ELEMENTS, value=_len_of("elements"),
                   display=lambda v: f"{v}/{N_ELEMENTS}",
                   help="Does the reply give emergency guidance, point the patient to their care team, and sound empathetic?"),
        ]

    judged = [o for o in ok if o.judge and "overall" in o.judge]
    if judged:
        criteria = ", ".join(JUDGE_CRITERIA[run.task])
        highlights.append(("Top judge score", _winner(judged, lambda o: o.judge["overall"], fmt=lambda v: f"{v:g} of 5")))
        panels.append(_panel(judged, label="Judge Score", direction="higher", scale=5,
                             value=lambda o: o.judge["overall"], display=lambda v: f"{v:g}/5",
                             help=f"{run.judge_model} rated each output 1–5 on {criteria}. This is the average; "
                                  f"the table view shows each score and the judge's reasoning."))

    if settings.SHOW_REFERENCE_METRICS and run.has_reference:
        panels += [
            _panel(ok, label="Reference Coverage", direction="higher", scale=1, value=lambda o: o.reference_coverage,
                   display=lambda v: f"{v:.2f}", help="Fraction of key reference points covered by the model."),
            _panel(ok, label="Missing Key Information", direction="lower", scale=1, value=lambda o: o.missing_key_info,
                   display=lambda v: f"{v:.2f}", help="Fraction of reference points not addressed."),
        ]

    return {
        "highlights": [{"label": label, **w} for label, w in highlights if w],
        "panels": [p for p in panels if p],
        "outputs": [_with_details(run, o) for o in outputs],
    }


def _with_details(run, out):
    out.details = details_for(run, out)
    return out


def details_for(run, out) -> list[tuple[str, str]]:
    """Label/value pairs for the table view."""
    a, rows = out.analysis or {}, []
    if not a:
        return rows
    if run.task == Task.FOLLOWUP:
        rows.append(("Questions", str(a.get("question_count", 0))))
        rows.append(("Topics", ", ".join(a.get("topics", [])) or "none"))
        if a.get("agreement") is not None:
            rows.append(("Agreement", f"{a['agreement']:.0%}"))
        if a.get("unique"):
            rows.append(("Only this model asked", " · ".join(a["unique"])))
        if a.get("repeated"):
            rows.append(("Repeated", " · ".join(a["repeated"])))
    else:
        rows.append(("Words", str(a.get("word_count", 0))))
        if a.get("grade_level") is not None:
            rows.append(("Grade level", str(a["grade_level"])))
        missing = [e for e in ELEMENTS if e not in a.get("elements", [])]
        rows.append(("Key elements", ", ".join(a.get("elements", [])) or "none"))
        if missing:
            rows.append(("Missing elements", ", ".join(missing)))
        rows.append(("Topics", ", ".join(a.get("topics", [])) or "none"))
    j = out.judge or {}
    if "overall" in j:
        scores = ", ".join(f"{k} {v}" for k, v in j["scores"].items())
        rows.append(("Judge", f"{j['overall']:g}/5 ({scores})"))
        if j.get("rationale"):
            rows.append(("Judge says", j["rationale"]))
    elif "error" in j:
        rows.append(("Judge", f"failed: {j['error']}"))
    return rows
