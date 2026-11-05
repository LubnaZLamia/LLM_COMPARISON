from django import forms

from .models import ITEM_CHOICES, REASONS, Confidence, Coverage, PeerAnnotation

LINKED = ("yes", "partly")   # item labels that say B has it somewhere


class AnnotatorForm(forms.Form):
    """Who is annotating. Kept in the session so it's typed once."""

    annotator = forms.CharField(
        max_length=60, label="Your name or initials",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Sam, or SK"}),
    )


class AnnotationForm(forms.Form):
    """The comparison of one model output with the clinician's answer.

    One radio field per clinician item (item_0, item_1, ...), and beside each a set of
    checkboxes for the parts of B that say it (link_0, link_1, ...). Then the overall
    questions. Which overall follow-up is required depends on "similar".
    """

    similar = forms.ChoiceField(choices=[("yes", "Yes"), ("no", "No")], widget=forms.RadioSelect,
                                error_messages={"required": "Please choose yes or no."})
    coverage = forms.TypedChoiceField(choices=Coverage.choices, coerce=int, required=False, widget=forms.RadioSelect)
    reasons = forms.MultipleChoiceField(choices=[(k, label) for k, (label, _) in REASONS.items()],
                                        required=False, widget=forms.CheckboxSelectMultiple)
    other_reason = forms.CharField(max_length=200, required=False,
                                   widget=forms.TextInput(attrs={"placeholder": "What else is different?"}))
    confidence = forms.ChoiceField(choices=Confidence.choices, widget=forms.RadioSelect,
                                   error_messages={"required": "Please choose one option."})
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note"}))
    seconds = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, items: list[str], units: list[str], instance: PeerAnnotation | None = None, **kwargs):
        if instance is not None and "initial" not in kwargs:
            kwargs["initial"] = self.initial_from(instance, items, units)
        super().__init__(*args, **kwargs)
        self.items, self.units = items, units
        unit_choices = [(str(j), str(j + 1)) for j in range(len(units))]
        for i, _ in enumerate(items):
            self.fields[f"item_{i}"] = forms.ChoiceField(
                choices=ITEM_CHOICES, widget=forms.RadioSelect,
                error_messages={"required": "Please choose an answer for this item."})
            self.fields[f"link_{i}"] = forms.MultipleChoiceField(
                choices=unit_choices, required=False, widget=forms.CheckboxSelectMultiple)

    @staticmethod
    def initial_from(instance: PeerAnnotation, items: list[str], units: list[str]) -> dict:
        initial = {
            "similar": {True: "yes", False: "no"}.get(instance.similar, ""),
            "coverage": instance.coverage,
            "reasons": instance.reasons,
            "other_reason": instance.other_reason,
            "confidence": instance.confidence,
            "comment": instance.comment,
        }
        # Earlier labels only apply if the clinician's items, and for the links B's parts, haven't changed since.
        if instance.reference_items == items:
            initial.update({f"item_{i}": label for i, label in enumerate(instance.item_labels)})
            if instance.candidate_units == units:
                initial.update({f"link_{i}": [str(j) for j in links] for i, links in enumerate(instance.item_links)})
        return initial

    @property
    def item_rows(self):
        return [(i + 1, text, self[f"item_{i}"], self[f"link_{i}"]) for i, text in enumerate(self.items)]

    def links(self) -> list[list[int]]:
        """Per item, the parts of B it points to. Not-covered items point nowhere."""
        data = self.cleaned_data
        return [sorted(int(j) for j in data.get(f"link_{i}", [])) if data.get(f"item_{i}") in LINKED else []
                for i in range(len(self.items))]

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

        if self.units:
            for i in range(len(self.items)):
                if data.get(f"item_{i}") in LINKED and not data.get(f"link_{i}"):
                    self.add_error(f"link_{i}", "Please pick the part of B that says this.")
        return data

    def save(self, output, annotator: str, existing: PeerAnnotation | None = None) -> PeerAnnotation:
        data = self.cleaned_data
        labels = [data[f"item_{i}"] for i in range(len(self.items))]
        links = self.links()
        obj = existing or PeerAnnotation(output=output, annotator=annotator)
        obj.reference_items = list(self.items)
        obj.item_labels = labels
        obj.item_score = PeerAnnotation.score_items(labels)
        obj.candidate_units = list(self.units)
        obj.item_links = links
        obj.unit_score = PeerAnnotation.score_units(links, len(self.units))
        obj.extra = PeerAnnotation.extra_band(obj.unit_score)
        obj.similar = data["similar"] == "yes"
        obj.coverage = data["coverage"]
        obj.reasons = list(data["reasons"])
        obj.other_reason = data["other_reason"].strip()
        obj.confidence = data["confidence"]
        obj.comment = data["comment"].strip()
        obj.seconds = data.get("seconds")
        obj.save()
        return obj
