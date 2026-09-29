import csv
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.dataset.management.commands.import_datasets import as_question_list, guess_category
from apps.dataset.models import Category, PatientMessage


class ImportDatasetsTests(TestCase):
    def _csv(self, name, header, rows):
        path = Path(tempfile.mkdtemp()) / name
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_numpy_style_question_lists_are_parsed(self):
        raw = "['Do you have nausea?'  'Any vomiting?'  'Any blood in your stools?']"
        self.assertEqual(as_question_list(raw), ["Do you have nausea?", "Any vomiting?", "Any blood in your stools?"])
        self.assertEqual(as_question_list(["a?", "b?"]), ["a?", "b?"])
        self.assertEqual(as_question_list(None), [])

    def test_category_is_guessed(self):
        self.assertEqual(guess_category("Can I get a refill of my medication?"), Category.MEDICATION)
        self.assertEqual(guess_category("My lab results look odd"), Category.TEST_RESULTS)

    def test_both_files_merge_on_the_same_message(self):
        questions = self._csv("questions.csv", ["Message", "EHR", "Questions"],
                              [["Stomach pains", "Age 35-40", "['Any nausea?' 'Any vomiting?']"]])
        responses = self._csv("responses.csv", ["Patient Message", "Patient EHR Summary", "Clinician Response"],
                              [["Stomach pains", "Age 35-40", "Please call the office."]])
        call_command("import_datasets", questions=str(questions), responses=str(responses))

        msg = PatientMessage.objects.get()          # one entry, both references
        self.assertEqual(msg.reference_questions, ["Any nausea?", "Any vomiting?"])
        self.assertEqual(msg.reference_response, "Please call the office.")
        self.assertEqual(msg.ehr_summary, "Age 35-40")
        self.assertEqual(msg.source, "questions.csv")

    def test_limit_and_replace(self):
        path = self._csv("q.csv", ["Message", "Questions"],
                         [[f"Message {i}", "['Any pain?']"] for i in range(5)])
        call_command("import_datasets", questions=str(path), limit=2)
        self.assertEqual(PatientMessage.objects.count(), 2)
        call_command("import_datasets", questions=str(path), limit=3, replace=True)
        self.assertEqual(PatientMessage.objects.count(), 3)
