from django.test import TestCase
from django.urls import reverse

from apps.comparisons.models import ComparisonRun, ModelOutput, Task
from apps.dataset.models import Category, PatientMessage
from apps.llms.models import LLMModel

from .models import DIMENSIONS, HumanRating, blind_labels
from .services import summary as rating_summary

FULL = {key: 4 for key in DIMENSIONS}


class RatingTests(TestCase):
    def setUp(self):
        msg = PatientMessage.objects.create(text="I feel dizzy.", category=Category.MEDICATION)
        self.run = ComparisonRun.objects.create(source_message=msg, message_text=msg.text, task=Task.FOLLOWUP)
        self.llms = [LLMModel.objects.create(display_name=f"Model {n}", provider=n, openrouter_id=f"x/{n}", order=i)
                     for i, n in enumerate("XYZ")]
        self.outputs = [ModelOutput.objects.create(run=self.run, llm=llm, model_name=llm.display_name,
                                                   openrouter_id=llm.openrouter_id, text=f"1. Question from {llm}")
                        for llm in self.llms]
        ModelOutput.objects.create(run=self.run, llm=self.llms[0], model_name="Broken",
                                   openrouter_id="x/broken", error="HTTP 429: busy")

    def _post(self, scores_by_output, rater="Dr. Lee"):
        session = self.client.session
        session["rater_name"] = rater
        session.save()
        data = {}
        for out, scores in scores_by_output.items():
            for key, value in scores.items():
                data[f"o{out.pk}-{key}"] = value
        return self.client.post(reverse("ratings:rate", args=[self.run.pk]), data)

    def test_blind_labels_skip_failures_and_shuffle_per_rater(self):
        a = blind_labels(self.run, "Dr. Lee")
        self.assertEqual(sorted(a.values()), ["Model A", "Model B", "Model C"])   # failed output excluded
        self.assertEqual(a, blind_labels(self.run, "dr. lee"))                    # stable for one rater

        # Different raters see the models in different orders.
        orders = {tuple(blind_labels(self.run, name)[o.pk] for o in self.outputs)
                  for name in ["Dr. Lee", "Dr. Khan", "Sam", "AB", "Dr. Ortiz", "JW"]}
        self.assertGreater(len(orders), 1)

    def test_rating_page_hides_model_names(self):
        session = self.client.session
        session["rater_name"] = "Dr. Lee"
        session.save()
        page = self.client.get(reverse("ratings:rate", args=[self.run.pk]))
        self.assertContains(page, "Model A")
        self.assertNotContains(page, "openrouter")
        for llm in self.llms:
            self.assertNotContains(page, llm.openrouter_id)

    def test_name_required_then_sent_back_to_the_run(self):
        """Regression: submitting the name used to crash with NoReverseMatch."""
        first = self.client.get(reverse("ratings:rate", args=[self.run.pk]))
        self.assertRedirects(first, reverse("ratings:list") + f"?next={self.run.pk}#rater",
                             fetch_redirect_response=False)

        named = self.client.post(reverse("ratings:list"), {"rater": "Dr. Lee", "next": str(self.run.pk)})
        self.assertRedirects(named, reverse("ratings:rate", args=[self.run.pk]), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("ratings:rate", args=[self.run.pk])).status_code, 200)

    def test_bad_next_value_does_not_crash(self):
        for bad in ["", "abc", "999999", "../../etc"]:
            response = self.client.post(reverse("ratings:list"), {"rater": "Dr. Lee", "next": bad})
            self.assertRedirects(response, reverse("ratings:list"), fetch_redirect_response=False)

    def test_run_list_counts_are_not_multiplied_by_the_join(self):
        """Regression: counting outputs and ratings in one query inflated both."""
        self._post({o: FULL for o in self.outputs}, rater="Dr. Lee")
        self._post({o: FULL for o in self.outputs}, rater="Dr. Khan")
        page = self.client.get(reverse("ratings:list"))
        run = next(r for r in page.context["runs"] if r.pk == self.run.pk)
        self.assertEqual(run.answered, 3)        # 3 good outputs, not 3 x 6 ratings
        self.assertEqual(run.rating_count, 6)
        self.assertEqual(run.rater_count, 2)

    def test_submit_stores_scores_and_reveals(self):
        response = self._post({o: FULL for o in self.outputs})
        self.assertRedirects(response, reverse("ratings:done", args=[self.run.pk]))
        self.assertEqual(HumanRating.objects.count(), 3)
        rating = HumanRating.objects.first()
        self.assertEqual(rating.overall, 4.0)
        self.assertTrue(rating.blind_label.startswith("Model "))

        done = self.client.get(reverse("ratings:done", args=[self.run.pk]))
        self.assertContains(done, "Model X")        # real names revealed here
        self.assertContains(done, "Which model was which")

    def test_incomplete_rating_is_rejected(self):
        partial = {key: 3 for key in list(DIMENSIONS)[:2]}
        response = self._post({self.outputs[0]: partial})
        self.assertEqual(HumanRating.objects.count(), 0)
        self.assertContains(response, "Every dimension needs a score")

    def test_rating_again_updates_instead_of_duplicating(self):
        self._post({o: FULL for o in self.outputs})
        self._post({o: {key: 2 for key in DIMENSIONS} for o in self.outputs})
        self.assertEqual(HumanRating.objects.count(), 3)
        self.assertEqual(HumanRating.objects.first().overall, 2.0)

    def test_averages_across_raters_and_panel(self):
        self._post({o: FULL for o in self.outputs}, rater="Dr. Lee")
        # Second rater scores the first model lower, the others the same.
        self._post({self.outputs[0]: {key: 2 for key in DIMENSIONS},
                    self.outputs[1]: FULL, self.outputs[2]: FULL}, rater="Dr. Khan")

        data = rating_summary.for_run(self.run)
        self.assertEqual(data["rater_count"], 2)
        by_name = {m["name"]: m for m in data["models"]}
        self.assertEqual(by_name["Model X"]["overall"], 3.0)      # (4 + 2) / 2
        self.assertEqual(by_name["Model X"]["raters"], 2)
        self.assertEqual(by_name["Model Y"]["overall"], 4.0)

        panel = rating_summary.panel(self.run)
        self.assertEqual(panel["label"], "Clinician Score")
        self.assertEqual(panel["rows"][0]["value"], "4/5")        # sorted best first

    def test_run_page_shows_clinician_score_and_csv(self):
        self._post({o: FULL for o in self.outputs})
        page = self.client.get(reverse("comparisons:run", args=[self.run.pk]))
        self.assertContains(page, "Clinician Score")
        csv_response = self.client.get(reverse("ratings:download"))
        self.assertContains(csv_response, "Dr. Lee")
        self.assertContains(csv_response, "factuality")
