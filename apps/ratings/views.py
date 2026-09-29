import csv

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.comparisons.models import ComparisonRun

from .forms import RaterForm, RatingForm
from .models import DIMENSIONS, SCALE, HumanRating, blind_labels
from .services import summary as rating_summary

RATER_SESSION_KEY = "rater_name"


def _rater(request) -> str:
    return (request.session.get(RATER_SESSION_KEY) or "").strip()


def run_list(request):
    """Pick a run to rate. Shows what each run already has."""
    rater = _rater(request)
    form = RaterForm(request.POST or None, initial={"rater": rater})
    if request.method == "POST" and form.is_valid():
        request.session[RATER_SESSION_KEY] = form.cleaned_data["rater"].strip()
        # "next" is a run id: send the rater straight on to that run.
        next_run = (request.POST.get("next") or "").strip()
        if next_run.isdigit() and ComparisonRun.objects.filter(pk=next_run).exists():
            return redirect("ratings:rate", pk=int(next_run))
        return redirect("ratings:list")

    runs = (
        ComparisonRun.objects.prefetch_related("outputs")
        .annotate(
            # distinct=True on each: without it, joining outputs and ratings multiplies the counts.
            answered=Count("outputs", filter=Q(outputs__error=""), distinct=True),
            rating_count=Count("ratings", distinct=True),
            rater_count=Count("ratings__rater", distinct=True),
            mine=Count("ratings", filter=Q(ratings__rater__iexact=rater) if rater else Q(pk=None), distinct=True),
        )
        .order_by("-created_at")[:60]
    )
    return render(request, "ratings/list.html", {
        "runs": runs, "form": form, "rater": rater,
        "total_ratings": HumanRating.objects.count(),
        "raters": HumanRating.objects.values_list("rater", flat=True).distinct().count(),
    })


def rate_run(request, pk):
    """Blind rating form: one card per model output, labelled Model A/B/C."""
    run = get_object_or_404(ComparisonRun.objects.prefetch_related("outputs__llm"), pk=pk)
    rater = _rater(request)
    if not rater:
        messages.info(request, "Add your name first — it is saved with each rating. You'll come straight back here.")
        return redirect(reverse("ratings:list") + f"?next={run.pk}#rater")

    labels = blind_labels(run, rater)
    outputs = sorted((o for o in run.outputs.all() if not o.error), key=lambda o: labels[o.pk])
    existing = {r.output_id: r for r in HumanRating.objects.filter(run=run, rater__iexact=rater)}

    if request.method == "POST":
        forms, valid = [], True
        for out in outputs:
            form = RatingForm(request.POST, prefix=f"o{out.pk}", instance=existing.get(out.pk))
            forms.append((out, form))
            valid = form.is_valid() and valid
        if valid:
            for out, form in forms:
                rating = form.save(commit=False)
                rating.output, rating.run, rating.rater = out, run, rater
                rating.blind_label = labels[out.pk]
                rating.save()
            messages.success(request, f"Saved {len(forms)} rating(s). Model names are revealed below.")
            return redirect(reverse("ratings:done", args=[run.pk]))
        messages.error(request, "Every dimension needs a score. The missing ones are marked below.")
        cards = [{"output": out, "label": labels[out.pk], "form": form} for out, form in forms]
    else:
        cards = [{
            "output": out, "label": labels[out.pk],
            "form": RatingForm(prefix=f"o{out.pk}", instance=existing.get(out.pk)),
        } for out in outputs]

    return render(request, "ratings/rate.html", {
        "run": run, "cards": cards, "rater": rater, "scale": SCALE,
        "dimensions": DIMENSIONS, "already_rated": bool(existing),
        "total_scores": len(cards) * len(DIMENSIONS),
    })


def done(request, pk):
    """Reveal which model was which, with this rater's scores."""
    run = get_object_or_404(ComparisonRun.objects.prefetch_related("outputs__llm"), pk=pk)
    rater = _rater(request)
    ratings = HumanRating.objects.filter(run=run, rater__iexact=rater).select_related("output")
    rows = sorted(
        ({"rating": r, "output": r.output, "scores": [getattr(r, key) for key in DIMENSIONS]} for r in ratings),
        key=lambda row: row["rating"].blind_label,
    )
    return render(request, "ratings/done.html", {
        "run": run, "rows": rows, "rater": rater,
        "summary": rating_summary.for_run(run),
        "dimension_labels": [(label, help_text) for label, help_text in DIMENSIONS.values()],
    })


def download_ratings(request):
    """Every rating, one row per rater per output — ready for agreement analysis."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="human_ratings.csv"'
    writer = csv.writer(response)
    writer.writerow(["rating_id", "created_at", "rater", "run_id", "task", "patient_message", "model",
                     "openrouter_id", "blind_label", *DIMENSIONS, "overall", "comment"])
    for r in HumanRating.objects.select_related("output", "run").order_by("run_id", "rater"):
        writer.writerow([
            r.pk, r.created_at.isoformat(), r.rater, r.run_id, r.run.get_task_display(), r.run.message_text,
            r.output.model_name, r.output.openrouter_id, r.blind_label,
            *[getattr(r, key) or "" for key in DIMENSIONS], r.overall or "", r.comment,
        ])
    return response
