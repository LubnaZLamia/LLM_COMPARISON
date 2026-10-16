"""Output-based analysis: compares models using only what they produced (no ground truth).

Follow-up questions
    question_count   how many questions the model asked
    repeated         questions that are near-duplicates of an earlier question from the same model
    topics           which clinical areas the questions touch (keyword checklist)
    agreement        share of the model's questions that at least one other model also asked
    unique           questions no other model asked

General response
    word_count       length of the reply
    grade_level      Flesch-Kincaid US school grade (lower = easier to read; <= 8 is patient-friendly)
    elements         emergency guidance / points to care team / empathetic language
    topics           which clinical areas the reply addresses

When the dataset message carries clinician ground truth, two more values are added:

    gt_matched / gt_total  follow-up: how many of the clinician's questions the model asked
    gt_score               share of the ground truth the model captured (0-1)
    gt_missed              what it missed

All checks are heuristic (word overlap and keyword lists). They are a quick, free,
transparent signal; use the optional judge model for a qualitative score.
"""
from __future__ import annotations

import re

from django.conf import settings

from apps.comparisons.models import Task

from . import metrics
from .metrics import content_tokens

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TOPICS = {
    "Onset & timing": r"\b(when|start|began|begin|how long|long have|since|duration|days?|weeks?|months?|sudden|often|frequen|time of day|constant|come and go)",
    "Severity": r"\b(severe|severity|scale|mild|moderate|intens|how bad|worse|worsen|getting better|rate)",
    "Description": r"\b(describe|feel like|what kind|type of|sharp|dull|throbbing|lighthead|spin|vertigo|where|location|itch|look like)",
    "Medications": r"\b(medication|medicine|dose|dosage|drug|prescri|supplement|pill|antibiotic|taking any|over-the-counter)",
    "Other symptoms": r"\b(other symptoms|any other|fever|nausea|vomit|rash|swelling|cough|headache|diarrh|fatigue|tired|pain)",
    "Red flags": r"\b(faint|pass(ed)? out|chest pain|short(ness)? of breath|trouble breathing|confus|weakness|numb|speech|speaking|vision|bleed|blood|fall|911|emergency)",
    "Medical history": r"\b(history|before|previous|in the past|condition|allerg|diagnos|chronic|surgery|pregnan)",
    "Impact & self-care": r"\b(daily|activities|sleep|work|tried|helped|relief|rest|fluids|hydrat|eat|drink|diet|exercise)",
}

ELEMENTS = {
    "Emergency guidance": r"\b(911|emergency|urgent care|\ber\b|right away|immediately|seek (immediate|urgent) )",
    "Points to care team": r"\b(call|contact|message|reach out|schedule|book|see|talk|speak|let)\b[^.]{0,50}\b(doctor|provider|care team|office|clinic|nurse|pharmacist|appointment|us|physician|team)\b",
    "Empathetic language": r"\b(sorry|understand|concern|frustrat|worr|thank you for|thanks for|must be|difficult|uncomfortable)",
}

_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")


def topics_in(text: str) -> list[str]:
    low = text.lower()
    return [name for name, pattern in TOPICS.items() if re.search(pattern, low)]


def extract_questions(text: str) -> list[str]:
    """Numbered/bulleted items if present, otherwise sentences ending in '?'."""
    items = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if m:
            items.append(m.group(1).replace("**", "").strip())
        elif line.strip() and items and not line.strip().endswith(":"):
            items[-1] += " " + line.strip()
    if items:
        return [i for i in items if i]
    return [s.strip() for s in re.findall(r"[^.?!\n]*\?", text) if s.strip()]


def similar(a: set[str], b: set[str], threshold: float = 0.6) -> bool:
    """Overlap coefficient on content words: shared / size of the smaller set."""
    if not a or not b:
        return False
    shared = len(a & b)
    smaller = min(len(a), len(b))
    if smaller <= 2:  # very short questions must match completely
        return shared == smaller
    return shared >= 2 and shared / smaller >= threshold


def _syllables(word: str) -> int:
    word = word.lower()
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", word)
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", word)))


def grade_level(text: str) -> float | None:
    """Flesch-Kincaid grade level (built in, so no extra dependency)."""
    words = re.findall(r"[A-Za-z]+(?:'[a-z]+)?", text)
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)|\n+", text) if re.search(r"[A-Za-z]", s)]
    if not words or not sentences:
        return None
    syllables = sum(_syllables(w) for w in words)
    grade = 0.39 * (len(words) / len(sentences)) + 11.8 * (syllables / len(words)) - 15.59
    return round(max(grade, 0), 1)


# ---------------------------------------------------------------------------
# Per-task analysis
# ---------------------------------------------------------------------------

def asks_about(gold_tokens: set[str], asked: list[set[str]]) -> bool:
    """True when one of the model's questions covers this clinician question.

    Recall-based on purpose: a model that asks "Any nausea or vomiting?" covers two
    of the clinician's separate questions, and should get credit for both.
    The bar is GT_MATCH_RECALL (default 0.5) of the clinician question's meaningful words.
    """
    if not gold_tokens:
        return False
    best = max((len(gold_tokens & q) / len(gold_tokens) for q in asked), default=0.0)
    return best >= settings.GT_MATCH_RECALL


def match_reference_questions(output: str, reference_questions: list[str]) -> tuple[list[str], list[str]]:
    """Split the clinician's questions into (asked by the model, missed)."""
    asked = [content_tokens(q) for q in extract_questions(output)] or [content_tokens(output)]
    matched, missed = [], []
    for gold in reference_questions:
        (matched if asks_about(content_tokens(gold), asked) else missed).append(gold)
    return matched, missed


def ground_truth_for_questions(output: str, reference: str) -> dict:
    gold = [q.strip() for q in reference.splitlines() if q.strip()]
    if not gold:
        return {}
    matched, missed = match_reference_questions(output, gold)
    return {
        "gt_matched": len(matched),
        "gt_total": len(gold),
        "gt_score": round(len(matched) / len(gold), 2),
        "gt_missed": missed,
    }


def ground_truth_for_response(output: str, reference: str) -> dict:
    """How much of the clinician's reply the model's reply covers."""
    scored = metrics.score(output, reference)
    if not scored:
        return {}
    covered = len(metrics.split_units(reference)) - len(scored.missed_points)
    return {
        "gt_matched": covered,
        "gt_total": len(metrics.split_units(reference)),
        "gt_score": scored.reference_coverage,
        "gt_missed": scored.missed_points,
    }


def analyze_followups(outputs: dict[int, str]) -> dict[int, dict]:
    """`outputs` maps an output id to its text. Returns analysis per id."""
    questions = {k: extract_questions(v) for k, v in outputs.items()}
    tokens = {k: [content_tokens(q) for q in qs] for k, qs in questions.items()}
    multiple_models = len(outputs) > 1
    results = {}
    for k, qs in questions.items():
        repeated = []
        for i, t in enumerate(tokens[k]):
            if any(similar(t, earlier, threshold=0.8) for earlier in tokens[k][:i]):
                repeated.append(qs[i])

        shared, unique = [], []
        for q, t in zip(qs, tokens[k]):
            others = (ot for other, lst in tokens.items() if other != k for ot in lst)
            (shared if any(similar(t, o) for o in others) else unique).append(q)

        results[k] = {
            "question_count": len(qs),
            "repeated": repeated,
            "topics": topics_in(" ".join(qs)),
            "agreement": round(len(shared) / len(qs), 2) if qs and multiple_models else None,
            "unique": unique if multiple_models else [],
        }
    return results


def analyze_responses(outputs: dict[int, str]) -> dict[int, dict]:
    results = {}
    for k, text in outputs.items():
        low = text.lower()
        results[k] = {
            "word_count": len(re.findall(r"\b[\w'-]+\b", text)),
            "grade_level": grade_level(text),
            "elements": [name for name, pattern in ELEMENTS.items() if re.search(pattern, low)],
            "topics": topics_in(text),
        }
    return results


def items_of(task: str, text: str) -> list[str]:
    """Split a text into the units the clinician comparison works on."""
    if task == Task.FOLLOWUP:
        return extract_questions(text) or ([text] if text.strip() else [])
    return metrics.split_units(text)


def reference_items(task: str, reference: str) -> list[str]:
    """Split the clinician's answer the same way: one question per line, or sentences."""
    if task == Task.FOLLOWUP:
        return [q.strip() for q in reference.splitlines() if q.strip()]
    return metrics.split_units(reference)


def _meaning_ground_truth(task: str, outputs: dict[int, str], reference: str) -> dict[int, dict]:
    """Clinician comparison by meaning. Returns {} if the alignment call is unavailable."""
    from . import matcher  # imported here so the lexical path never touches the API

    gold = reference_items(task, reference)
    if not gold or not matcher.available():
        return {}
    try:
        scored = matcher.match_many(gold, dict(outputs), task)
    except Exception:  # offline, no key, rate limited — the lexical score still stands
        return {}
    return {
        key: {
            "gt_score": result.score,
            "gt_matched": result.matched,
            "gt_total": result.total,
            "gt_missed": result.missed,
            "gt_covered": result.covered,
            "gt_method": "meaning",
        }
        for key, result in scored.items()
    }


def analyze(task: str, outputs: dict[int, str], reference: str = "", method: str | None = None) -> dict[int, dict]:
    """Analyse every output. `reference` is the clinician ground truth, when the dataset has one.

    `method` is one of lexical / meaning / both (default: the MATCH_METHOD setting).
    In `both`, the meaning score is the headline and the lexical score is kept
    beside it under `*_lexical` keys as the baseline to compare against.
    """
    if not outputs:
        return {}
    method = (method or settings.MATCH_METHOD).lower()
    is_followup = task == Task.FOLLOWUP
    results = analyze_followups(outputs) if is_followup else analyze_responses(outputs)

    if not reference.strip():
        return results

    lexical = ground_truth_for_questions if is_followup else ground_truth_for_response
    lexical_results = {key: lexical(text, reference) for key, text in outputs.items()}
    semantic_results = (_meaning_ground_truth(task, outputs, reference)
                        if method in ("meaning", "both") else {})

    for key in outputs:
        lex = dict(lexical_results.get(key) or {})
        sem = semantic_results.get(key)
        if method == "lexical" or not sem:
            if lex:
                lex["gt_method"] = "lexical"
                if method != "lexical":
                    lex["gt_fallback"] = True   # semantic was asked for but unavailable
            results[key].update(lex)
            continue
        results[key].update(sem)
        if method == "both" and lex:
            results[key].update({f"{k}_lexical": v for k, v in lex.items()})
    return results
