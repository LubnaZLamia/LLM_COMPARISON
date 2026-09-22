from django import forms

from apps.dataset.models import Category, PatientMessage
from apps.llms.models import LLMModel

from .models import Task


class CompareForm(forms.Form):
    SOURCE_EXAMPLE = "example"
    SOURCE_CUSTOM = "custom"

    source = forms.ChoiceField(choices=[(SOURCE_EXAMPLE, "Example"), (SOURCE_CUSTOM, "Custom")], initial=SOURCE_EXAMPLE)
    message = forms.ModelChoiceField(queryset=PatientMessage.objects.all(), required=False)
    custom_text = forms.CharField(required=False, widget=forms.Textarea)
    custom_category = forms.ChoiceField(choices=Category.choices, required=False, initial=Category.OTHER)
    save_to_dataset = forms.BooleanField(required=False)
    use_judge = forms.BooleanField(required=False)
    task = forms.ChoiceField(choices=Task.choices, initial=Task.FOLLOWUP)
    models = forms.ModelMultipleChoiceField(
        queryset=LLMModel.objects.filter(is_active=True),
        error_messages={"required": "Select at least one model."},
    )

    def clean(self):
        data = super().clean()
        if data.get("source") == self.SOURCE_CUSTOM:
            if not (data.get("custom_text") or "").strip():
                self.add_error("custom_text", "Write a patient message, or pick an example.")
        elif not data.get("message"):
            self.add_error("message", "Pick an example message, or write your own.")
        return data
