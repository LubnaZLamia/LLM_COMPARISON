from django import forms

from .models import ITEM_CHOICES, REASONS, Confidence, Coverage, Extra, PeerAnnotation


class AnnotatorForm(forms.Form):
    """Who is annotating. Kept in the session so it's typed once."""

    annotator = forms.CharField(
        max_length=60, label="Your name or initials",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Sam, or SK"}),
    )


class AnnotationForm(forms.Form):
    """The comparison of one model output with the clinician's answer.

    One radio field per clinician item (item_0, item_1, ...), then the overall questions.
    Which overall follow-up is required depends on the answer to "similar".
    """

    similar = forms.ChoiceField(choices=[("yes", "Yes"), ("no", "No")], widget=forms.RadioSelect,
                                error_messages={"required": "Please choose yes or no."})
    coverage = forms.TypedChoiceField(choices=Coverage.choices, coerce=int, required=False, widget=forms.RadioSelect)
    reasons = forms.MultipleChoiceField(choices=[(k, label) for k, (label, _) in REASONS.items()],
                                        required=False, widget=forms.CheckboxSelectMultiple)
    other_reason = forms.CharField(max_length=200, required=False,
                                   widget=forms.TextInput(attrs={"placeholder": "What else is different?"}))
    extra = forms.ChoiceField(choices=Extra.choices, widget=forms.RadioSelect,
                              error_messages={"required": "Please choose one option."})
    confidence = forms.ChoiceField(choices=Confidence.choices, widget=forms.RadioSelect,
                                   error_messages={"required": "Please choose one option."})
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"}))
    seconds = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, items: list[str], instance: PeerAnnotation | None = None, **kwargs):
        if instance is not None and "initial" not in kwargs:
            kwargs["initial"] = self.initial_from(instance, items)
        super().__init__(*args, **kwargs)
        self.items = items
        for i, _ in enumerate(items):
            self.fields[f"item_{i}"] = forms.ChoiceField(
                choices=ITEM_CHOICES, widget=forms.RadioSelect,
                error_messages={"required": "Please choose an answer for this item."})

    @staticmethod
    def initial_from(instance: PeerAnnotation, items: list[str]) -> dict:
        initial = {
            "similar": {True: "yes", False: "no"}.get(instance.similar, ""),
            "coverage": instance.coverage,
            "reasons": instance.reasons,
            "other_reason": instance.other_reason,
            "extra": instance.extra,
            "confidence": instance.confidence,
            "comment": instance.comment,
        }
        # Earlier labels only apply if the clinician's items haven't changed since.
        if instance.reference_items == items:
            initial.update({f"item_{i}": label for i, label in enumerate(instance.item_labels)})
        return initial

    @property
    def item_rows(self):
        return [(i + 1, text, self[f"item_{i}"]) for i, text in enumerate(self.items)]

    def clean(self):
        data = super().clean()
        if data.get("similar") == "yes":
            if data.get("coverage") in (None, ""):
                self.add_error("coverage", "Please choose roughly how much of A is covered.")
            data["reasons"], data["other_reason"] = [], ""
        elif data.get("similar") == "no":
            if not data.get("reasons"):
                self.add_error("reasons", "Please choose at least one reason.")
            elif "other" in data["reasons"] and not data.get("other_reason", "").strip():
                self.add_error("other_reason", "Please add a short note about the other reason.")
            data["coverage"] = None
        return data

    def save(self, output, annotator: str, existing: PeerAnnotation | None = None) -> PeerAnnotation:
        data = self.cleaned_data
        labels = [data[f"item_{i}"] for i in range(len(self.items))]
        obj = existing or PeerAnnotation(output=output, annotator=annotator)
        obj.reference_items = list(self.items)
        obj.item_labels = labels
        obj.item_score = PeerAnnotation.score_items(labels)
        obj.similar = data["similar"] == "yes"
        obj.coverage = data["coverage"]
        obj.reasons = list(data["reasons"])
        obj.other_reason = data["other_reason"].strip()
        obj.extra = data["extra"]
        obj.confidence = data["confidence"]
        obj.comment = data["comment"].strip()
        obj.seconds = data.get("seconds")
        obj.save()
        return obj
