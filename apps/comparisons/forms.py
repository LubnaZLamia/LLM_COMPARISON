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
            return data

        message = data.get("message")
        if not message:
            self.add_error("message", "Pick an example message, or write your own.")
        # Each task has its own dataset. The page only lists eligible messages, but a
        # stale form (task changed in another tab, back button, script) could still
        # post a message the clinician never answered for this task.
        elif not message.has_reference_for(data.get("task")):
            wanted = ("clinician follow-up questions" if data.get("task") == Task.FOLLOWUP
                      else "clinician reply")
            self.add_error("message", f"That message has no {wanted}, so this task can't be scored "
                                      f"against the clinician. Pick one from the list for this task.")
        return data
