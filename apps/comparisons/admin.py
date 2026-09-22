from django.contrib import admin

from .models import ComparisonRun, ModelOutput


class ModelOutputInline(admin.TabularInline):
    model = ModelOutput
    extra = 0
    fields = ("model_name", "text", "error", "latency_ms", "reference_coverage", "missing_key_info", "relative_length")
    readonly_fields = fields


@admin.register(ComparisonRun)
class ComparisonRunAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "task", "category", "message_text", "mock_mode")
    list_filter = ("task", "category", "mock_mode")
    inlines = [ModelOutputInline]
