import csv
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.comparisons.models import Task

from .forms import AnnotationForm, AnnotatorForm
from .models import ITEM_CHOICES, REASONS, PeerAnnotation
from .services import context, queue, stats

ANNOTATOR_SESSION_KEY = "annotator_name"
SKIPPED_SESSION_KEY = "annotation_skipped"
HISTORY_SESSION_KEY = "annotation_history"
HISTORY_LENGTH = 50


def _annotator(request) -> str:
    return (request.session.get(ANNOTATOR_SESSION_KEY) or "").strip()


def _task(request) -> str:
    value = request.GET.get("task") or request.POST.get("task") or ""
    return value if value in Task.values else ""


def _with_task(url: str, task: str) -> str:
    return f"{url}?{urlencode({'task': task})}" if task else url


def _history(request) -> list[int]:
    return list(request.session.get(HISTORY_SESSION_KEY) or [])


def _remember(request, pk: int) -> None:
    """Add an item to the back history, unless it's the one already on top."""
    history = _history(request)
    if not history or history[-1] != pk:
        request.session[HISTORY_SESSION_KEY] = (history + [pk])[-HISTORY_LENGTH:]


def home(request):
    """Who is annotating, how far they are, and the button to start."""
    annotator = _annotator(request)
    task = _task(request)
    form = AnnotatorForm(request.POST or None, initial={"annotator": annotator})
    if request.method == "POST" and form.is_valid():
        request.session[ANNOTATOR_SESSION_KEY] = form.cleaned_data["annotator"].strip()
        request.session[SKIPPED_SESSION_KEY] = []
        request.session[HISTORY_SESSION_KEY] = []
        next_item = (request.POST.get("next") or "").strip()
        if next_item.isdigit() and queue.eligible().filter(pk=next_item).exists():
            return redirect(_with_task(reverse("annotations:item", args=[int(next_item)]), task))
        return redirect(_with_task(reverse("annotations:home"), task))

    mine = (PeerAnnotation.objects.filter(annotator__iexact=annotator).select_related("output__run")[:25]
            if annotator else [])
    return render(request, "annotations/home.html", {
        "form": form,
        "annotator": annotator,
        "task": task,
        "tasks": queue.task_counts(),
        "progress": queue.progress(annotator, task),
        "can_go_back": bool(annotator and _history(request)),
        "mine": mine,
        "total_annotations": PeerAnnotation.objects.count(),
        "annotators": PeerAnnotation.objects.values_list("annotator", flat=True).distinct().count(),
    })


def next_item(request):
    """Send the annotator to the next output they haven't done. ?skip=<id> passes on one."""
    annotator = _annotator(request)
    task = _task(request)
    if not annotator:
        messages.info(request, "Please add your name first. It's saved with your answers.")
        return redirect(_with_task(reverse("annotations:home"), task) + "#annotator")
    skipped = set(request.session.get(SKIPPED_SESSION_KEY) or [])
    skip = request.GET.get("skip", "")
    if skip.isdigit():
        skipped.add(int(skip))
        request.session[SKIPPED_SESSION_KEY] = sorted(skipped)
    output = queue.next_for(annotator, task, skipped)
    if output is None:
        request.session[SKIPPED_SESSION_KEY] = []
        messages.success(request, "You have annotated every item in this set. Thank you!")
        return redirect(_with_task(reverse("annotations:home"), task))
    return redirect(_with_task(reverse("annotations:item", args=[output.pk]), task))


def back(request):
    """Undo for the queue: open the item seen before this one, skipped or saved.

    ?from=<id> is the item being left; without it (from the home page) this reopens
    the most recent item. Each press goes one further back, like Ctrl+Z.
    """
    task = _task(request)
    history = _history(request)
    leaving = request.GET.get("from", "")
    if leaving.isdigit():
        while history and history[-1] == int(leaving):
            history.pop()
    target = None
    while history and target is None:
        pk = history.pop()
        target = pk if queue.eligible().filter(pk=pk).exists() else None
    request.session[HISTORY_SESSION_KEY] = history
    if target is None:
        messages.info(request, "There's no earlier item to go back to.")
        if leaving.isdigit() and queue.eligible().filter(pk=int(leaving)).exists():
            request.session[HISTORY_SESSION_KEY] = [int(leaving)]
            return redirect(_with_task(reverse("annotations:item", args=[int(leaving)]), task))
        return redirect(_with_task(reverse("annotations:home"), task))
    # Going back to a skipped item un-skips it, so Next won't jump over it again.
    request.session[SKIPPED_SESSION_KEY] = [p for p in request.session.get(SKIPPED_SESSION_KEY) or [] if p != target]
    return redirect(_with_task(reverse("annotations:item", args=[target]), task))


def annotate(request, pk):
    """Compare one model output with the clinician's answer."""
    output = get_object_or_404(queue.eligible(), pk=pk)
    task = _task(request)
    annotator = _annotator(request)
    if not annotator:
        messages.info(request, "Please add your name first, and you'll come straight back to this item.")
        return redirect(_with_task(reverse("annotations:home"), task) + f"{'&' if task else '?'}next={pk}#annotator")

    _remember(request, pk)
    items, units = queue.items_for(output), queue.candidate_units(output)
    existing = PeerAnnotation.objects.filter(output=output, annotator__iexact=annotator).first()
    if request.method == "POST":
        form = AnnotationForm(request.POST, items=items, units=units)
        if form.is_valid():
            form.save(output, annotator, existing)
            request.session[SKIPPED_SESSION_KEY] = [p for p in request.session.get(SKIPPED_SESSION_KEY) or [] if p != pk]
            stop = request.POST.get("then") == "home"
            messages.success(request, ("Updated your annotation." if existing else "Saved.")
                             + ("" if stop else " Here is the next one."))
            return redirect(_with_task(reverse("annotations:home" if stop else "annotations:next"), task))
        messages.error(request, "A few answers are still missing. They're highlighted below.")
    else:
        form = AnnotationForm(items=items, units=units, instance=existing)

    is_followup = output.run.task == Task.FOLLOWUP
    return render(request, "annotations/annotate.html", {
        "output": output,
        "run": output.run,
        "form": form,
        "task": task,
        "annotator": annotator,
        "existing": existing,
        "is_followup": is_followup,
        "patient": context.for_run(output.run),
        "segments": queue.segments(output),
        "item_choices": ITEM_CHOICES,
        "reasons": REASONS,
        "progress": queue.progress(annotator, task),
        "can_go_back": len(_history(request)) > 1,
    })


def results(request):
    task = _task(request)
    return render(request, "annotations/results.html", {
        "task": task,
        "tasks": queue.task_counts(),
        "data": stats.build(task),
        "target": settings.ANNOTATION_TARGET,
    })


def _blank(value):
    return "" if value is None else value


def _csv_response(filename: str):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response, csv.writer(response)


def download(request):
    """One row per annotation, with the automatic scores beside the human ones."""
    response, writer = _csv_response("peer_annotations.csv")
    writer.writerow(["annotation_id", "updated_at", "annotator", "output_id", "run_id", "task", "model",
                     "openrouter_id", "items_total", "items_covered", "items_partly", "items_not_covered",
                     "human_item_score", "units_total", "units_matched", "human_unit_score", "human_f1",
                     "similar", "coverage_band", "overall_score",
                     "reasons", "other_reason", "extra", "confidence", "seconds", "auto_meaning_score",
                     "auto_lexical_score", "comment"])
    for a in PeerAnnotation.objects.select_related("output__run").order_by("output_id", "annotator"):
        auto = stats.automatic(a.output)
        counts = a.counts
        units = len(a.candidate_units)
        writer.writerow([
            a.pk, a.updated_at.isoformat(), a.annotator, a.output_id, a.output.run_id, a.output.run.task,
            a.output.model_name, a.output.openrouter_id, len(a.item_labels), counts["yes"], counts["partly"],
            counts["no"], a.item_score, units or "", a.units_matched if units else "",
            _blank(a.unit_score), _blank(a.f1),
            {True: "yes", False: "no"}.get(a.similar, ""),
            a.get_coverage_display() if a.coverage is not None else "", a.overall if a.overall is not None else "",
            "|".join(a.reasons), a.other_reason, a.extra, a.confidence, a.seconds or "",
            auto.get("meaning", {}).get("score", ""), auto.get("lexical", {}).get("score", ""), a.comment,
        ])
    return response


def download_items(request):
    """One row per clinician item per annotation: the human label next to each matcher's call."""
    response, writer = _csv_response("peer_annotation_items.csv")
    writer.writerow(["annotation_id", "annotator", "output_id", "run_id", "task", "model", "item_number",
                     "clinician_item", "human_label", "b_parts", "b_text", "auto_meaning_covered",
                     "auto_lexical_covered"])
    yes_no = {True: "yes", False: "no"}
    for a in PeerAnnotation.objects.select_related("output__run").order_by("output_id", "annotator"):
        auto = stats.automatic(a.output)
        for number, (item, label) in enumerate(zip(a.reference_items, a.item_labels), 1):
            links = a.item_links[number - 1] if number <= len(a.item_links) else []
            writer.writerow([
                a.pk, a.annotator, a.output_id, a.output.run_id, a.output.run.task, a.output.model_name, number,
                item, label, " ".join(str(j + 1) for j in links),
                " | ".join(a.candidate_units[j] for j in links if j < len(a.candidate_units)),
                yes_no.get(auto.get("meaning", {}).get("covered", {}).get(item), ""),
                yes_no.get(auto.get("lexical", {}).get("covered", {}).get(item), ""),
            ])
    return response


def download_units(request):
    """One row per part of the model's text per annotation: which clinician items point to it."""
    response, writer = _csv_response("peer_annotation_units.csv")
    writer.writerow(["annotation_id", "annotator", "output_id", "run_id", "task", "model", "part_number",
                     "b_text", "clinician_items"])
    for a in PeerAnnotation.objects.select_related("output__run").order_by("output_id", "annotator"):
        for j, text in enumerate(a.candidate_units):
            items = [str(i + 1) for i, links in enumerate(a.item_links) if j in links]
            writer.writerow([
                a.pk, a.annotator, a.output_id, a.output.run_id, a.output.run.task, a.output.model_name, j + 1,
                text, " ".join(items),
            ])
    return response
