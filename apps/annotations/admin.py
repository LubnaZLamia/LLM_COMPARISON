from django.contrib import admin

from .models import PeerAnnotation


@admin.register(PeerAnnotation)
class PeerAnnotationAdmin(admin.ModelAdmin):
    list_display = ("id", "annotator", "output", "item_score", "unit_score", "similar", "coverage", "extra", "confidence", "updated_at")
    list_filter = ("similar", "confidence", "extra", "output__run__task")
    search_fields = ("annotator", "comment", "output__model_name")
    readonly_fields = ("created_at", "updated_at")
