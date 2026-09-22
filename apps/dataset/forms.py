from django import forms

from .models import PatientMessage


class PatientMessageForm(forms.ModelForm):
    class Meta:
        model = PatientMessage
        fields = ["text", "category", "reference_followup", "reference_response", "notes", "is_featured"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. I have been feeling dizzy after taking my medication."}),
            "reference_followup": forms.Textarea(attrs={"rows": 6, "placeholder": "One question per line"}),
            "reference_response": forms.Textarea(attrs={"rows": 6}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "text": "Patient message",
            "reference_followup": "Reference follow-up questions",
            "reference_response": "Reference response",
            "is_featured": "Show in examples on Compare page",
        }


class CSVImportForm(forms.Form):
    file = forms.FileField(
        help_text="CSV with columns: text, category, reference_followup, reference_response, notes"
    )
