"""What the peer annotations say, and how well the automatic Clinician Match agrees with them.

Three questions, all computed from the database, no API calls:

* Per model: how similar to the clinician do people find each model's outputs?
* Human vs automatic: per output (Spearman, mean gap) and per clinician item
  (agreement and Cohen's kappa) for both matchers, meaning and word overlap.
* Human vs human: on outputs two people annotated, how often they gave the same
  item label, and the same yes/no answer. This is the ceiling a metric can reach.

The links from clinician items to parts of the model's text add the other direction:
how much of each model's text the clinician also said.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from statistics import mean

from apps.comparisons.management.commands.validate_metrics import spearman
from apps.comparisons.models import Task

from ..models import REASONS, PeerAnnotation
from .queue import items_for

METHODS = [("meaning", "Clinician Match (meaning)"), ("lexical", "Clinician Match (words)")]


def cohen_kappa(a: list, b: list) -> float | None:
    """Unweighted Cohen's kappa for two equal-length label lists."""
    n = len(a)
    if n < 2:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    expected = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    return None if expected == 1 else round((observed - expected) / (1 - expected), 3)


def automatic(output) -> dict[str, dict]:
    """The stored automatic result per matcher: {"meaning": {"score": .., "covered": {item: bool}}, ...}.

    Runs scored before the meaning matcher existed only carry the word-overlap result
    (no gt_method key), and runs scored with MATCH_METHOD=both keep it under *_lexical.
    """
    a = output.analysis or {}
    items = items_for(output)
    found = {}
    if a.get("gt_method") == "meaning" and a.get("gt_score") is not None:
        covered = set(a.get("gt_covered") or [])
        found["meaning"] = {"score": a["gt_score"], "covered": {item: item in covered for item in items}}
    if a.get("gt_score_lexical") is not None:
        missed = set(a.get("gt_missed_lexical") or [])
        found["lexical"] = {"score": a["gt_score_lexical"], "covered": {item: item not in missed for item in items}}
    elif a.get("gt_method") != "meaning" and a.get("gt_score") is not None:
        missed = set(a.get("gt_missed") or [])
        found["lexical"] = {"score": a["gt_score"], "covered": {item: item not in missed for item in items}}
    return found


def _round(value, places=3):
    return None if value is None else round(value, places)


def build(task: str = "") -> dict:
    annotations = list(PeerAnnotation.objects.select_related("output__run", "output__llm").order_by("output_id", "pk"))
    if task in Task.values:
        annotations = [a for a in annotations if a.output.run.task == task]

    by_output: dict[int, list[PeerAnnotation]] = defaultdict(list)
    for ann in annotations:
        by_output[ann.output_id].append(ann)

    outputs = {}
    for pk, anns in by_output.items():
        output = anns[0].output
        item_scores = [a.item_score for a in anns if a.item_score is not None]
        unit_scores = [a.unit_score for a in anns if a.unit_score is not None]
        f1s = [a.f1 for a in anns if a.f1 is not None]
        overall = [a.overall for a in anns if a.overall is not None]
        outputs[pk] = {
            "output": output,
            "human": mean(item_scores) if item_scores else None,
            "unit": mean(unit_scores) if unit_scores else None,
            "f1": mean(f1s) if f1s else None,
            "overall": mean(overall) if overall else None,
            "similar": mean(1.0 if a.similar else 0.0 for a in anns if a.similar is not None) if anns else None,
            "auto": automatic(output),
            "n": len(anns),
        }

    return {
        "annotations": len(annotations),
        "annotators": len({a.annotator.strip().lower() for a in annotations}),
        "outputs": len(outputs),
        "models": _per_model(outputs),
        "output_level": _output_level(outputs),
        "item_level": _item_level(annotations),
        "agreement": _inter_annotator(by_output),
        "reasons": _reasons(annotations),
    }


def _per_model(outputs: dict) -> list[dict]:
    groups = defaultdict(list)
    for row in outputs.values():
        groups[row["output"].model_name].append(row)
    table = []
    for name, rows in sorted(groups.items()):
        def avg(values):
            values = [v for v in values if v is not None]
            return _round(mean(values)) if values else None
        table.append({
            "name": name,
            "output": rows[0]["output"],
            "outputs": len(rows),
            "annotations": sum(r["n"] for r in rows),
            "human": avg(r["human"] for r in rows),
            "unit": avg(r["unit"] for r in rows),
            "f1": avg(r["f1"] for r in rows),
            "overall": avg(r["overall"] for r in rows),
            "similar": avg(r["similar"] for r in rows),
            "meaning": avg(r["auto"].get("meaning", {}).get("score") for r in rows),
            "lexical": avg(r["auto"].get("lexical", {}).get("score") for r in rows),
        })
    table.sort(key=lambda r: -(r["human"] or 0))
    return table


def _output_level(outputs: dict) -> list[dict]:
    """Does the automatic score move with the human one? One row per matcher."""
    result = []
    for key, label in METHODS:
        pairs = [(r["auto"][key]["score"], r["human"]) for r in outputs.values()
                 if key in r["auto"] and r["human"] is not None]
        result.append({
            "key": key, "label": label, "n": len(pairs),
            "spearman": _round(spearman([p[0] for p in pairs], [p[1] for p in pairs])) if pairs else None,
            "gap": _round(mean(p[0] - p[1] for p in pairs)) if pairs else None,
            "abs_gap": _round(mean(abs(p[0] - p[1]) for p in pairs)) if pairs else None,
        })
    return result


def _item_level(annotations: list[PeerAnnotation]) -> list[dict]:
    """Per clinician item: did the matcher call it covered where the person did?

    "Partly" has no counterpart in a yes/no matcher, so those items are counted but left out.
    """
    result = []
    for key, label in METHODS:
        human, machine, partly = [], [], 0
        missed_by_machine = found_by_machine_only = 0
        for ann in annotations:
            auto = automatic(ann.output).get(key)
            if not auto:
                continue
            for item, person in zip(ann.reference_items, ann.item_labels):
                if item not in auto["covered"]:
                    continue
                if person == "partly":
                    partly += 1
                    continue
                h, m = person == "yes", auto["covered"][item]
                human.append(h)
                machine.append(m)
                missed_by_machine += h and not m
                found_by_machine_only += m and not h
        n = len(human)
        result.append({
            "key": key, "label": label, "n": n, "partly": partly,
            "agreement": _round(sum(h == m for h, m in zip(human, machine)) / n) if n else None,
            "kappa": cohen_kappa(human, machine),
            "missed": missed_by_machine,       # person says covered, matcher says not
            "extra": found_by_machine_only,    # matcher says covered, person says not
        })
    return result


def _inter_annotator(by_output: dict) -> dict:
    item_a, item_b, similar_a, similar_b, gaps, pairs = [], [], [], [], [], 0
    matched_a, matched_b = [], []
    for anns in by_output.values():
        for a, b in combinations(anns, 2):
            pairs += 1
            if a.reference_items == b.reference_items:
                item_a += a.item_labels
                item_b += b.item_labels
            if a.candidate_units and a.candidate_units == b.candidate_units:
                # Did both find a counterpart in A for this part of B?
                for j in range(len(a.candidate_units)):
                    matched_a.append(any(j in links for links in a.item_links))
                    matched_b.append(any(j in links for links in b.item_links))
            if a.similar is not None and b.similar is not None:
                similar_a.append(a.similar)
                similar_b.append(b.similar)
            if a.item_score is not None and b.item_score is not None:
                gaps.append(abs(a.item_score - b.item_score))
    n = len(item_a)
    return {
        "pairs": pairs,
        "items": n,
        "item_agreement": _round(sum(x == y for x, y in zip(item_a, item_b)) / n) if n else None,
        "item_kappa": cohen_kappa(item_a, item_b),
        "similar_agreement": _round(sum(x == y for x, y in zip(similar_a, similar_b)) / len(similar_a)) if similar_a else None,
        "similar_kappa": cohen_kappa(similar_a, similar_b),
        "score_gap": _round(mean(gaps)) if gaps else None,
        "units": len(matched_a),
        "unit_agreement": _agreement(matched_a, matched_b),
        "unit_kappa": cohen_kappa(matched_a, matched_b),
    }


def _agreement(a: list, b: list) -> float | None:
    return _round(sum(x == y for x, y in zip(a, b)) / len(a)) if a else None


def _reasons(annotations: list[PeerAnnotation]) -> list[dict]:
    counts = Counter(r for a in annotations if a.similar is False for r in a.reasons)
    total = sum(1 for a in annotations if a.similar is False)
    return [{"key": key, "label": label, "help": help_text, "count": counts.get(key, 0),
             "pct": round(100 * counts.get(key, 0) / total) if total else 0}
            for key, (label, help_text) in REASONS.items()]
