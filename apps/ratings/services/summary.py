"""Aggregates human ratings for display on a run."""
from __future__ import annotations

from statistics import mean

from apps.ratings.models import DIMENSIONS, HumanRating


def for_run(run) -> dict:
    """Per-model averages across every rater, plus a per-dimension breakdown."""
    ratings = list(HumanRating.objects.filter(run=run).select_related("output"))
    if not ratings:
        return {}

    by_output: dict[int, list[HumanRating]] = {}
    for rating in ratings:
        by_output.setdefault(rating.output_id, []).append(rating)

    models = []
    for out in run.outputs.all():
        mine = by_output.get(out.pk, [])
        overalls = [r.overall for r in mine if r.overall is not None]
        if not overalls:
            continue
        models.append({
            "output": out,
            "name": out.model_name,
            "color": out.color,
            "mark": out.mark,
            "ink": out.ink,
            "raters": len(mine),
            "overall": round(mean(overalls), 2),
            # Aligned with DIMENSIONS order so the template can just loop.
            "dimensions": [
                round(mean(values), 2) if (values := [getattr(r, key) for r in mine if getattr(r, key) is not None]) else "–"
                for key in DIMENSIONS
            ],
            "comments": [(r.rater, r.comment) for r in mine if r.comment.strip()],
        })

    models.sort(key=lambda m: m["overall"], reverse=True)
    return {
        "models": models,
        "rater_count": len({r.rater.lower() for r in ratings}),
        "rating_count": len(ratings),
        "dimension_labels": [label for label, _ in DIMENSIONS.values()],
    }


def panel(run) -> dict | None:
    """A metric card for the comparison results page, in summary.build()'s shape."""
    data = for_run(run)
    if not data or not data["models"]:
        return None
    top = max(m["overall"] for m in data["models"])
    rows = [{
        "name": m["name"], "color": m["color"], "mark": m["mark"], "ink": m["ink"], "raw": m["overall"],
        "value": f"{m['overall']:g}/5", "pct": round(m["overall"] / 5 * 100),
        "best": m["overall"] == top and len(data["models"]) > 1,
    } for m in data["models"]]
    raters = data["rater_count"]
    return {
        "label": "Clinician Score",
        "arrow": "↑",
        "icon": "user-check",
        "rows": rows,
        "help": f"Blind clinician ratings from {raters} rater{'s' if raters != 1 else ''} "
                f"({data['rating_count']} ratings), averaged over fluency, coherence, relevance, "
                f"factual correctness, completeness and safety.",
    }
