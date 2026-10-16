"""Turns stored analysis into what the results page shows: highlight tiles, metric panels, detail rows."""
from __future__ import annotations

from django.conf import settings

from apps.comparisons.models import Task

from .analysis import ELEMENTS, TOPICS
from .prompts import JUDGE_CRITERIA

N_TOPICS = len(TOPICS)
N_ELEMENTS = len(ELEMENTS)


def _panel(outputs, *, label, help, value, display, direction=None, scale=None, icon="chart", detail=None):
    """Build one metric panel.

    value(out) -> number or None; display(v) -> str; direction: 'higher' | 'lower' | None;
    scale: fixed max for bar length (defaults to the largest value).
    """
    rows = []
    for out in outputs:
        v = value(out)
        if v is None:
            continue
        rows.append({"name": out.model_name, "color": out.color, "mark": out.mark, "ink": out.ink,
                     "raw": v, "value": display(v)})
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
    return {"label": label, "arrow": arrow, "help": help, "rows": rows, "icon": icon}


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
    tile = {"names": names, "value": fmt(best)}
    if len(winners) == 1:  # one clear winner: show its mark on the tile
        tile |= {"mark": winners[0].mark, "ink": winners[0].ink}
    return tile


def build(run) -> dict:
    outputs = list(run.outputs.all())
    ok = [o for o in outputs if not o.error]
    panels, highlights = [], []

    # Three headline metrics per task. Everything else (unique / repeated questions,
    # topic lists, per-criterion judge scores) lives in the table view.
    if run.task == Task.FOLLOWUP:
        highlights += [
            ("Asked the most", "help", _winner(ok, _a("question_count"), fmt=lambda v: f"{v} questions")),
            ("Covered the most ground", "grid", _winner(ok, _len_of("topics"), fmt=lambda v: f"{v} of {N_TOPICS} topics")),
            ("Most in agreement", "users", _winner(ok, _a("agreement"), fmt=lambda v: f"{v:.0%} shared")),
        ]
        panels += [
            _panel(ok, label="Questions Asked", icon="help", value=_a("question_count"), display=str,
                   help="How many follow-up questions the model asked. More isn't always better."),
            _panel(ok, label="Topics Covered", icon="grid", direction="higher", scale=N_TOPICS, value=_len_of("topics"),
                   display=lambda v: f"{v}/{N_TOPICS}",
                   help="How many of 8 clinical areas the questions touch: timing, severity, description, medications, other symptoms, red flags, history, daily impact."),
            _panel(ok, label="Agreement", icon="users", direction="higher", scale=1, value=_a("agreement"),
                   display=lambda v: f"{v:.0%}",
                   help="Share of this model's questions that another model also asked. High means it stuck to the obvious questions; low means it went its own way."),
        ]
    else:
        highlights += [
            ("Longest reply", "ruler", _winner(ok, _a("word_count"), fmt=lambda v: f"{v} words")),
            ("Easiest to read", "book", _winner(ok, _a("grade_level"), pick=min, fmt=lambda v: f"grade {v}")),
            ("Most complete", "shield", _winner(ok, _len_of("elements"), fmt=lambda v: f"{v} of {N_ELEMENTS} elements")),
        ]
        panels += [
            _panel(ok, label="Reply Length", icon="ruler", value=_a("word_count"), display=lambda v: f"{v} w",
                   help="Word count of the reply."),
            _panel(ok, label="Reading Level", icon="book", direction="lower", scale=16, value=_a("grade_level"),
                   display=lambda v: f"{v}",
                   help="US school grade needed to read the reply (Flesch-Kincaid). 8 or below is patient-friendly."),
            _panel(ok, label="Key Elements", icon="shield", direction="higher", scale=N_ELEMENTS, value=_len_of("elements"),
                   display=lambda v: f"{v}/{N_ELEMENTS}",
                   help="Does the reply give emergency guidance, point the patient to their care team, and sound empathetic?"),
        ]

    # Clinician ratings, if anyone has rated this run on the Rate page.
    from apps.ratings.services import summary as rating_summary

    human = rating_summary.panel(run)
    if human:
        panels.append(human)
        best = max(human["rows"], key=lambda r: r["raw"])
        if len(human["rows"]) > 1:
            highlights.append(("Clinicians' pick", "trophy", {
                "names": best["name"], "value": f"{best['raw']:g} of 5",
                "mark": best["mark"], "ink": best["ink"],
            }))

    # Judge Score is always the fourth card, so it's clear the option exists.
    judged = [o for o in ok if o.judge and "overall" in o.judge]
    criteria = ", ".join(JUDGE_CRITERIA[run.task])
    if judged:
        highlights.append(("Top judge score", "scale", _winner(judged, lambda o: o.judge["overall"], fmt=lambda v: f"{v:g} of 5")))
        panels.append(_panel(judged, label="Judge Score", icon="scale", direction="higher", scale=5,
                             value=lambda o: o.judge["overall"], display=lambda v: f"{v:g}/5",
                             help=f"Another LLM ({run.judge_model}) rated each output 1–5 on {criteria}. "
                                  f"Table view shows each score and the judge's reasoning."))
    else:
        panels.append({
            "label": "Judge Score", "arrow": "↑", "rows": [], "placeholder": True, "icon": "scale",
            "help": f"Not scored for this run. Tick “Score with judge model” before generating and another LLM "
                    f"rates each output 1–5 on {criteria}. It costs one extra API call per model.",
        })

    # The lexical baseline, shown beside the meaning score when both were computed.
    if any(o.analysis.get("gt_score_lexical") is not None for o in ok):
        panels.insert(0, _panel(ok, label="Clinician Match (words)", icon="type", direction="higher", scale=1,
                                value=_a("gt_score_lexical"), display=lambda v: f"{v:.0%}",
                                help="The original baseline: a clinician item counts as covered when half its "
                                     "meaningful words appear in one model sentence. Kept so the meaning score "
                                     "has something to be measured against."))

    # Ground truth: a card either way, so it is never a mystery why it is missing.
    gt_label = "Clinician Match"
    if run.has_reference and any(o.analysis.get("gt_score") is not None for o in ok):
        by_meaning = any(o.analysis.get("gt_method") == "meaning" for o in ok)
        subject = ("the clinician's own follow-up questions this model also asked"
                   if run.task == Task.FOLLOWUP else
                   "the points in the clinician's own reply this model's reply also covers")
        gt_help = (f"How much of {subject}. "
                   + ("Aligned by a small model reading both texts, so a correct paraphrase still counts. "
                      if by_meaning else
                      "Matched on shared wording, so a correct paraphrase can be missed. ")
                   + "Table view lists what each model missed and its closest matches.")
        panels.insert(0, _panel(ok, label=gt_label, icon="target", direction="higher", scale=1, value=_a("gt_score"),
                                display=lambda v: f"{v:.0%}", help=gt_help))
        highlights.insert(0, ("Closest to the clinician", "target", _winner(ok, _a("gt_score"), fmt=lambda v: f"{v:.0%} match")))
    else:
        missing = ("There is no clinician answer for this message, so there is nothing to compare against. "
                   "Pick an example message to see this score.")
        if not run.source_message_id:
            missing = "You wrote this message yourself, so there is no clinician answer to compare against."
        panels.insert(0, {"label": gt_label, "arrow": "↑", "rows": [], "placeholder": True, "icon": "target", "help": missing})

    return {
        "has_human_ratings": bool(human),
        "has_ground_truth": run.has_reference,
        "has_failures": any(o.error for o in outputs),
        "highlights": [{"label": label, "icon": icon, **w} for label, icon, w in highlights if w],
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
    if a.get("gt_score") is not None:
        label = "Clinician questions matched" if run.task == Task.FOLLOWUP else "Clinician reply covered"
        how = "by meaning" if a.get("gt_method") == "meaning" else "by wording"
        rows.append((f"{label} ({how})", f"{a['gt_matched']}/{a['gt_total']} ({a['gt_score']:.0%})"))
        if a.get("gt_score_lexical") is not None:
            rows.append(("Same, by wording only (baseline)",
                         f"{a['gt_matched_lexical']}/{a['gt_total_lexical']} ({a['gt_score_lexical']:.0%})"))
        if a.get("gt_missed"):
            rows.append(("Missed from ground truth", " · ".join(a["gt_missed"])))
        if a.get("gt_covered"):
            rows.append(("Covered from the clinician", " · ".join(a["gt_covered"])))
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
