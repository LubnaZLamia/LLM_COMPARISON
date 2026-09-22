"""Reference-based comparison metrics.

These are lightweight lexical metrics (no extra dependencies):

* Reference Coverage  - fraction of reference key points that appear in the output.
* Missing Key Info    - fraction of reference key points NOT addressed (1 - coverage).
* Relative Length     - output word count / reference word count.

A "key point" is one line (or sentence) of the reference. A key point counts as
covered when at least COVER_THRESHOLD of its content words appear in some single
line/sentence of the model output.

They are a quick signal, not a clinical judgement. Paraphrases that use very
different words can be missed; swap `_is_covered` for an embedding or LLM-judge
check later if you need semantic matching.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

COVER_THRESHOLD = 0.5

STOPWORDS = set(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can could did do does doing down during each few for from further had
    has have having he her here hers herself him himself his how i if in into is it its itself just
    me more most my myself no nor not now of off on once only or other our ours ourselves out over own
    same she should so some such than that the their theirs them themselves then there these they this
    those through to too under until up very was we were what when where which while who whom why will
    with would you your yours yourself yourselves please any also e g eg like let us know""".split()
)

_LIST_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    for suffix in ("ing", "edly", "ed", "ies", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def content_tokens(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1}


def split_units(text: str) -> list[str]:
    """Split into list items if the text is a list, otherwise into sentences."""
    lines = [_LIST_PREFIX.sub("", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) >= 2:
        units = lines
    else:
        units = re.split(r"(?<=[.!?])\s+", text.strip())
    return [u for u in units if content_tokens(u)]


def word_count(text: str) -> int:
    return len(_WORD.findall(text.lower()))


@dataclass
class MetricResult:
    reference_coverage: float
    missing_key_info: float
    relative_length: float
    missed_points: list[str] = field(default_factory=list)


def _is_covered(point_tokens: set[str], output_units_tokens: list[set[str]]) -> bool:
    if not point_tokens:
        return True
    best = max((len(point_tokens & u) / len(point_tokens) for u in output_units_tokens), default=0.0)
    return best >= COVER_THRESHOLD


def score(output: str, reference: str) -> MetricResult | None:
    """Return metrics for `output` against `reference`, or None if there is no usable reference."""
    ref_points = split_units(reference or "")
    if not ref_points or not output.strip():
        return None

    output_units = [content_tokens(u) for u in split_units(output)] or [content_tokens(output)]

    missed = [p for p in ref_points if not _is_covered(content_tokens(p), output_units)]
    coverage = 1 - len(missed) / len(ref_points)
    ref_words = word_count(reference) or 1
    return MetricResult(
        reference_coverage=round(coverage, 2),
        missing_key_info=round(1 - coverage, 2),
        relative_length=round(word_count(output) / ref_words, 2),
        missed_points=missed,
    )
