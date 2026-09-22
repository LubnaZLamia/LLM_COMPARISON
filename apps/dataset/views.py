import csv
import io

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import CSVImportForm, PatientMessageForm
from .models import Category, PatientMessage

CSV_FIELDS = ["text", "category", "reference_followup", "reference_response", "notes"]


class MessageListView(ListView):
    model = PatientMessage
    template_name = "dataset/list.html"
    context_object_name = "items"
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        cat = self.request.GET.get("category", "")
        if q:
            qs = qs.filter(Q(text__icontains=q) | Q(notes__icontains=q))
        if cat:
            qs = qs.filter(category=cat)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            categories=Category.choices,
            q=self.request.GET.get("q", ""),
            current_category=self.request.GET.get("category", ""),
            total=PatientMessage.objects.count(),
        )
        return ctx


class MessageCreateView(CreateView):
    model = PatientMessage
    form_class = PatientMessageForm
    template_name = "dataset/form.html"
    success_url = reverse_lazy("dataset:list")

    def form_valid(self, form):
        messages.success(self.request, "Message added to the dataset.")
        return super().form_valid(form)


class MessageUpdateView(UpdateView):
    model = PatientMessage
    form_class = PatientMessageForm
    template_name = "dataset/form.html"
    success_url = reverse_lazy("dataset:list")

    def form_valid(self, form):
        messages.success(self.request, "Message updated.")
        return super().form_valid(form)


class MessageDeleteView(DeleteView):
    model = PatientMessage
    template_name = "dataset/confirm_delete.html"
    success_url = reverse_lazy("dataset:list")

    def form_valid(self, form):
        messages.success(self.request, "Message deleted.")
        return super().form_valid(form)


def export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="patient_messages.csv"'
    writer = csv.writer(response)
    writer.writerow(CSV_FIELDS)
    for m in PatientMessage.objects.all():
        writer.writerow([getattr(m, f) for f in CSV_FIELDS])
    return response


def import_csv(request):
    form = CSVImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        raw = form.cleaned_data["file"].read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
        valid_categories = {c for c, _ in Category.choices}
        label_to_value = {label.lower(): value for value, label in Category.choices}
        created = skipped = 0
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                skipped += 1
                continue
            cat = (row.get("category") or "").strip()
            cat = cat if cat in valid_categories else label_to_value.get(cat.lower(), Category.OTHER)
            PatientMessage.objects.create(
                text=text,
                category=cat,
                reference_followup=row.get("reference_followup", "") or "",
                reference_response=row.get("reference_response", "") or "",
                notes=row.get("notes", "") or "",
            )
            created += 1
        messages.success(request, f"Imported {created} message(s). Skipped {skipped} empty row(s).")
        return redirect("dataset:list")
    return render(request, "dataset/import.html", {"form": form, "fields": CSV_FIELDS})
