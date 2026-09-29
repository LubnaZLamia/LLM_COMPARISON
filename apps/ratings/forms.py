from django import forms

from .models import DIMENSIONS, SCALE, HumanRating


class RaterForm(forms.Form):
    """Who is rating. Kept in the session so it's typed once."""

    rater = forms.CharField(
        max_length=60, label="Your name or initials",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Dr. Rahman, or LR"}),
    )


class RatingForm(forms.ModelForm):
    """Scores for one output. Rendered once per blinded card."""

    class Meta:
        model = HumanRating
        fields = list(DIMENSIONS) + ["comment"]
        widgets = {
            **{key: forms.RadioSelect(choices=SCALE) for key in DIMENSIONS},
            "comment": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"}),
        }
        labels = {key: label for key, (label, _) in DIMENSIONS.items()}
        help_texts = {key: help_text for key, (_, help_text) in DIMENSIONS.items()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in DIMENSIONS:
            self.fields[key].required = True   # a rating with gaps isn't much use
