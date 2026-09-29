from django.contrib import admin

from .models import HumanRating


@admin.register(HumanRating)
class HumanRatingAdmin(admin.ModelAdmin):
    list_display = ("created_at", "rater", "run", "model_name", "blind_label", "overall")
    list_filter = ("rater", "run__task")
    search_fields = ("rater", "comment", "output__model_name")

    @admin.display(description="Model")
    def model_name(self, obj):
        return obj.output.model_name

    @admin.display(description="Overall")
    def overall(self, obj):
        return obj.overall
