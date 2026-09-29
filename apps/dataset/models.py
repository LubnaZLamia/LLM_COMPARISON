from django.db import models


class Category(models.TextChoices):
    MEDICATION = "medication", "Medication"
    SYMPTOMS = "symptoms", "Symptoms"
    TEST_RESULTS = "test_results", "Test / Results"
    TREATMENT_RESPONSE = "treatment_response", "Treatment Response"
    SCHEDULING = "scheduling", "Scheduling / Admin"
    OTHER = "other", "Other"


class PatientMessage(models.Model):
    """A patient portal message plus optional clinician-written reference outputs.

    The reference outputs are what the comparison metrics score model outputs against.
    """

    text = models.TextField(help_text="The message as the patient would write it.")
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.OTHER)
    reference_followup = models.TextField(
        blank=True,
        help_text="Reference follow-up questions, one per line. Used to score the 'Follow-up Questions' task.",
    )
    reference_response = models.TextField(
        blank=True,
        help_text="Reference reply to the patient. Used to score the 'General Response' task.",
    )
    ehr_summary = models.TextField(
        blank=True,
        help_text="Chart context (demographics, problem list, diagnoses, medications) sent to the models with the message.",
    )
    source = models.CharField(
        max_length=60, blank=True, help_text="Where this message came from, e.g. the dataset file name.",
    )
    notes = models.TextField(blank=True, help_text="Internal notes (source, reviewer, etc.).")
    is_featured = models.BooleanField(
        default=True, help_text="Show this message in the example list on the Compare page."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "id"]

    def __str__(self):
        return self.text[:60]

    def reference_for(self, task: str) -> str:
        from apps.comparisons.models import Task

        return self.reference_followup if task == Task.FOLLOWUP else self.reference_response

    @property
    def has_references(self) -> bool:
        return bool(self.reference_followup.strip() or self.reference_response.strip())

    def has_reference_for(self, task: str) -> bool:
        return bool(self.reference_for(task).strip())

    @property
    def reference_questions(self) -> list[str]:
        """The clinician's follow-up questions, one per line."""
        return [q.strip() for q in self.reference_followup.splitlines() if q.strip()]
