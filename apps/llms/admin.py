from django.contrib import admin

from .models import LLMModel


@admin.register(LLMModel)
class LLMModelAdmin(admin.ModelAdmin):
    list_display = ("display_name", "provider", "openrouter_id", "is_active", "selected_by_default", "is_judge", "order")
    list_editable = ("is_active", "selected_by_default", "is_judge", "order")
    search_fields = ("display_name", "provider", "openrouter_id")
