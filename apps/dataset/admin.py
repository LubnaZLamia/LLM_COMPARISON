from django.contrib import admin

from .models import PatientMessage


@admin.register(PatientMessage)
class PatientMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "short_text", "category", "is_featured", "has_references", "updated_at")
    list_filter = ("category", "is_featured")
    search_fields = ("text", "notes")

    @admin.display(description="Message")
    def short_text(self, obj):
        return str(obj)

    @admin.display(boolean=True)
    def has_references(self, obj):
        return obj.has_references
