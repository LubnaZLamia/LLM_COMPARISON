import csv

from django.contrib import messages
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.dataset.models import Category, PatientMessage
from apps.llms.models import LLMModel

from .forms import CompareForm
from .models import ComparisonRun, Task
from .services import summary
from .services.judge import judge_model_id
from .services.runner import retry_failed, run_comparison


def compare(request, pk=None):
    run = get_object_or_404(ComparisonRun.objects.prefetch_related("outputs__llm"), pk=pk) if pk else None

    if request.method == "POST":
        form = CompareForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            if data["source"] == CompareForm.SOURCE_CUSTOM:
                text = data["custom_text"].strip()
                category = data["custom_category"] or Category.OTHER
                source = None
                if data["save_to_dataset"]:
                    source = PatientMessage.objects.create(text=text, category=category, is_featured=True)
                    messages.info(request, "Saved your message to the dataset.")
            else:
                source = data["message"]
                text, category = source.text, source.category
            new_run = run_comparison(
                message_text=text, task=data["task"], llms=list(data["models"]),
                category=category, source_message=source, use_judge=data["use_judge"],
            )
            return redirect(reverse("comparisons:run", args=[new_run.pk]) + "#results")
    else:
        initial = {"task": Task.FOLLOWUP, "models": LLMModel.objects.filter(is_active=True, selected_by_default=True)}
        if run:
            initial["task"] = run.task
            initial["models"] = [o.llm for o in run.outputs.all() if o.llm]
            initial["use_judge"] = bool(run.judge_model)
            if run.source_message_id:
                initial.update(source=CompareForm.SOURCE_EXAMPLE, message=run.source_message_id)
            else:
                initial.update(source=CompareForm.SOURCE_CUSTOM, custom_text=run.message_text, custom_category=run.category)
        form = CompareForm(initial=initial)

    selected_models = {str(getattr(m, "pk", m)) for m in (form["models"].value() or [])}
    selected_message = str(form["message"].value() or "")

    # Each task has its own dataset: a message is only offered for a task when the
    # clinician answered that task, so every run can be scored. Messages with no
    # ground truth at all never appear in the list.
    # Richest cases first: real dataset messages carry a chart summary, the built-in
    # demo messages don't, so this floats the interesting ones to the top of the list.
    featured = (PatientMessage.objects.filter(is_featured=True)
                .exclude(reference_followup="", reference_response="")
                .annotate(has_chart=Case(When(ehr_summary="", then=Value(1)), default=Value(0),
                                         output_field=IntegerField()))
                .order_by("has_chart", "category", "id"))
    task_value = form["task"].value() or Task.FOLLOWUP
    for_task = featured.exclude(**{"reference_followup" if task_value == Task.FOLLOWUP else "reference_response": ""})
    if not selected_message and form["source"].value() != CompareForm.SOURCE_CUSTOM and for_task.exists():
        selected_message = str(for_task.first().pk)

    context = {
        "form": form,
        "run": run,
        "examples": featured,
        "llms": LLMModel.objects.filter(is_active=True),
        "tasks": Task,
        "categories": Category.choices,
        "selected_models": selected_models,
        "selected_message": selected_message,
        "judge_model": judge_model_id(),
        "view_mode": request.GET.get("view", "side"),
    }
    if run:
        context["summary"] = summary.build(run)
    return render(request, "comparisons/compare.html", context)


def retry_run(request, pk):
    """Re-run only the models that failed, and merge the results into the same run."""
    run = get_object_or_404(ComparisonRun, pk=pk)
    if request.method == "POST":
        names = retry_failed(run)
        if names:
            still_failing = [o.model_name for o in run.outputs.all() if o.error]
            if still_failing:
                messages.warning(request, "Still failing: " + ", ".join(still_failing))
            else:
                messages.success(request, "Retried " + ", ".join(names) + " — all models answered.")
    return redirect(reverse("comparisons:run", args=[run.pk]) + "#results")


def download_run(request, pk):
    run = get_object_or_404(ComparisonRun.objects.prefetch_related("outputs__llm"), pk=pk)
    data = summary.build(run)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="comparison_run_{run.pk}.csv"'
    writer = csv.writer(response)
    writer.writerow(["run_id", "created_at", "task", "category", "patient_message", "ehr_summary", "model",
                     "openrouter_id", "latency_ms", "output", "error", "ground_truth_score", "ground_truth_matched",
                     "analysis", "judge_model", "judge_overall"])
    for o in data["outputs"]:
        writer.writerow([
            run.pk, run.created_at.isoformat(), run.get_task_display(), run.get_category_display(), run.message_text,
            run.ehr_summary, o.model_name, o.openrouter_id, o.latency_ms, o.text, o.error,
            (o.analysis or {}).get("gt_score", ""),
            f"{(o.analysis or {}).get('gt_matched', '')}/{(o.analysis or {}).get('gt_total', '')}".strip("/"),
            " | ".join(f"{k}: {v}" for k, v in o.details),
            run.judge_model, (o.judge or {}).get("overall", ""),
        ])
    return response
