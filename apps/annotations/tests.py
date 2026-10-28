"""Peer annotation tests. No API calls: everything works from stored runs."""
import csv
import io

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.comparisons.models import ComparisonRun, ModelOutput, Task
from apps.llms.models import LLMModel

from .forms import AnnotationForm
from .models import PeerAnnotation
from .services import queue, stats
from .services.stats import cohen_kappa

QUESTIONS = "Do you have a fever?\nWhen did it start?\nAny blood in your stool?"


def make_run(task=Task.FOLLOWUP, reference=QUESTIONS, mock=False, message="My stomach hurts."):
    return ComparisonRun.objects.create(message_text=message, task=task, reference_text=reference, mock_mode=mock)


def make_output(run, llm, text="1. Any fever?\n2. When did this start?", error="", analysis=None):
    return ModelOutput.objects.create(run=run, llm=llm, model_name=llm.display_name, openrouter_id=llm.openrouter_id,
                                      text=text, error=error, analysis=analysis or {})


def form_data(labels, similar="yes", coverage=50, reasons=(), other="", extra="little", confidence="sure"):
    data = {f"item_{i}": label for i, label in enumerate(labels)}
    data.update({"similar": similar, "extra": extra, "confidence": confidence, "other_reason": other, "seconds": "42"})
    if coverage is not None:
        data["coverage"] = str(coverage)
    if reasons:
        data["reasons"] = list(reasons)
    return data


class Fixture(TestCase):
    def setUp(self):
        self.gpt = LLMModel.objects.create(display_name="Model GPT", provider="OpenAI", openrouter_id="openai/x")
        self.claude = LLMModel.objects.create(display_name="Model Claude", provider="Anthropic", openrouter_id="anthropic/y")
        self.run = make_run()
        self.a = make_output(self.run, self.gpt)
        self.b = make_output(self.run, self.claude, text="1. Do you have a fever?\n2. Any bleeding?")

    def login(self, name="Sam"):
        session = self.client.session
        session["annotator_name"] = name
        session.save()

    def annotate(self, output, annotator, labels, **kwargs):
        form = AnnotationForm(form_data(labels, **kwargs), items=queue.items_for(output))
        self.assertTrue(form.is_valid(), form.errors)
        return form.save(output, annotator)


class QueueTests(Fixture):
    def test_eligible_leaves_out_unusable_outputs(self):
        make_output(self.run, self.gpt, error="HTTP 429")
        make_output(self.run, self.gpt, text="")
        make_output(make_run(mock=True), self.gpt)
        make_output(make_run(reference=""), self.gpt)
        self.assertEqual(set(queue.eligible()), {self.a, self.b})

    def test_task_filter(self):
        reply = make_output(make_run(task=Task.RESPONSE, reference="Please call the office."), self.gpt, text="Call us.")
        self.assertEqual(set(queue.eligible(Task.RESPONSE)), {reply})
        self.assertEqual(set(queue.eligible(Task.FOLLOWUP)), {self.a, self.b})

    def test_items_already_started_come_first(self):
        self.annotate(self.b, "Alex", ["yes", "no", "no"])
        self.assertEqual(queue.next_for("Sam"), self.b)

    @override_settings(ANNOTATION_TARGET=1)
    def test_items_at_target_come_last(self):
        self.annotate(self.b, "Alex", ["yes", "no", "no"])
        self.assertEqual(queue.next_for("Sam"), self.a)

    def test_own_items_skips_and_done(self):
        self.annotate(self.a, "sam", ["yes", "no", "no"])      # names match case-insensitively
        self.assertEqual(queue.next_for("Sam"), self.b)
        self.assertEqual(queue.next_for("Sam", skip={self.b.pk}), self.b)  # only skipped left: offered again
        self.annotate(self.b, "Sam", ["yes", "no", "no"])
        self.assertIsNone(queue.next_for("Sam"))

    def test_items_and_candidate_lists(self):
        self.assertEqual(queue.items_for(self.a), QUESTIONS.splitlines())
        self.assertEqual(queue.candidate_items(self.a), ["Any fever?", "When did this start?"])

    def test_progress(self):
        self.annotate(self.a, "Sam", ["yes", "no", "no"])
        p = queue.progress("Sam")
        self.assertEqual((p["total"], p["mine"], p["left"], p["pct"], p["started"]), (2, 1, 1, 50, 1))


class FormTests(Fixture):
    def form(self, **kwargs):
        labels = kwargs.pop("labels", ["yes", "partly", "no"])
        return AnnotationForm(form_data(labels, **kwargs), items=queue.items_for(self.a))

    def saved(self, **kwargs):
        form = self.form(**kwargs)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save(self.a, "Sam")

    def test_yes_needs_a_band_and_drops_reasons(self):
        self.assertIn("coverage", self.form(coverage=None).errors)
        form = self.form(reasons=["contradicts"])
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["reasons"], [])

    def test_no_needs_a_reason_and_other_needs_text(self):
        self.assertIn("reasons", self.form(similar="no", coverage=None).errors)
        self.assertIn("other_reason", self.form(similar="no", coverage=None, reasons=["other"]).errors)
        form = self.form(similar="no", coverage=50, reasons=["other"], other="Different body part")
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["coverage"])

    def test_every_item_is_required(self):
        form = self.form(labels=["yes", "no"])
        self.assertIn("item_2", form.errors)

    def test_save_scores_items(self):
        saved = self.saved()
        self.assertEqual(saved.item_labels, ["yes", "partly", "no"])
        self.assertEqual(saved.item_score, 0.5)
        self.assertEqual(saved.overall, 0.625)    # "Most of it" band: 50-75%, middle 62.5%
        self.assertEqual(saved.seconds, 42)

    def test_no_counts_as_zero_overall(self):
        saved = self.saved(similar="no", reasons=["different_topics"])
        self.assertIs(saved.similar, False)
        self.assertEqual(saved.overall, 0.0)
        self.assertEqual(saved.reason_labels, ["Different topics"])

    def test_edit_restores_labels_unless_items_changed(self):
        saved = self.saved()
        initial = AnnotationForm(items=queue.items_for(self.a), instance=saved).initial
        self.assertEqual(initial["item_1"], "partly")
        changed = AnnotationForm(items=["Something else"], instance=saved).initial
        self.assertNotIn("item_0", changed)


class ViewTests(Fixture):
    def test_home_and_name(self):
        resp = self.client.get(reverse("annotations:home"))
        self.assertContains(resp, "Please add your name or initials to begin")
        resp = self.client.post(reverse("annotations:home"), {"annotator": " Sam "})
        self.assertRedirects(resp, reverse("annotations:home"))
        self.assertEqual(self.client.session["annotator_name"], "Sam")
        self.assertContains(self.client.get(reverse("annotations:home")), "Start annotating")

    def test_home_empty_state(self):
        ModelOutput.objects.all().delete()
        self.assertContains(self.client.get(reverse("annotations:home")), "Nothing to annotate yet")

    def test_name_first_then_back_to_the_item(self):
        url = reverse("annotations:item", args=[self.a.pk])
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse("annotations:home") + f"?next={self.a.pk}#annotator",
                             fetch_redirect_response=False)
        resp = self.client.post(reverse("annotations:home"), {"annotator": "Sam", "next": self.a.pk})
        self.assertRedirects(resp, url)

    def test_item_page_hides_the_model(self):
        self.login()
        resp = self.client.get(reverse("annotations:item", args=[self.a.pk]))
        self.assertContains(resp, "Any blood in your stool?")
        self.assertContains(resp, "When did this start?")
        self.assertNotContains(resp, "Model GPT")
        self.assertNotContains(resp, "openai/x")

    def test_save_then_next_then_update(self):
        self.login()
        url = reverse("annotations:item", args=[self.a.pk])
        resp = self.client.post(url, form_data(["yes", "yes", "no"]))
        self.assertRedirects(resp, reverse("annotations:next"), fetch_redirect_response=False)
        self.assertEqual(PeerAnnotation.objects.get().item_score, 0.667)
        resp = self.client.get(reverse("annotations:next"))
        self.assertRedirects(resp, reverse("annotations:item", args=[self.b.pk]), fetch_redirect_response=False)
        self.client.post(url, form_data(["no", "no", "no"], similar="no", coverage=None, reasons=["misses_most"]))
        self.assertEqual(PeerAnnotation.objects.count(), 1)
        self.assertEqual(PeerAnnotation.objects.get().item_score, 0.0)

    def test_save_and_stop_keeps_the_task(self):
        self.login()
        data = form_data(["yes", "no", "no"]) | {"then": "home", "task": "followup"}
        resp = self.client.post(reverse("annotations:item", args=[self.a.pk]), data)
        self.assertRedirects(resp, reverse("annotations:home") + "?task=followup")

    def test_invalid_post_shows_errors(self):
        self.login()
        resp = self.client.post(reverse("annotations:item", args=[self.a.pk]), form_data(["yes"], coverage=None))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please choose an answer for this item.")
        self.assertFalse(PeerAnnotation.objects.exists())

    def test_ineligible_output_is_404(self):
        self.login()
        mock = make_output(make_run(mock=True), self.gpt)
        self.assertEqual(self.client.get(reverse("annotations:item", args=[mock.pk])).status_code, 404)

    def test_next_when_done_and_skip(self):
        self.login()
        resp = self.client.get(reverse("annotations:next") + f"?skip={self.b.pk}")
        self.assertRedirects(resp, reverse("annotations:item", args=[self.a.pk]), fetch_redirect_response=False)
        self.annotate(self.a, "Sam", ["yes", "no", "no"])
        self.annotate(self.b, "Sam", ["yes", "no", "no"])
        resp = self.client.get(reverse("annotations:next"))
        self.assertRedirects(resp, reverse("annotations:home"))

    def test_nav_link(self):
        self.assertContains(self.client.get(reverse("comparisons:compare")), 'href="/annotate/"')


class StatsTests(Fixture):
    def setUp(self):
        super().setUp()
        items = QUESTIONS.splitlines()
        # Stored the way MATCH_METHOD=both stores it: meaning first, word overlap beside it.
        self.a.analysis = {"gt_method": "meaning", "gt_score": 0.667, "gt_covered": items[:2], "gt_missed": items[2:],
                           "gt_score_lexical": 0.333, "gt_missed_lexical": items[1:]}
        self.a.save()
        # A run scored before the meaning matcher existed: word overlap only, no gt_method.
        self.b.analysis = {"gt_score": 0.333, "gt_missed": items[1:]}
        self.b.save()

    def test_kappa(self):
        self.assertEqual(cohen_kappa(["y", "y", "n", "n"], ["y", "y", "n", "n"]), 1.0)
        self.assertEqual(cohen_kappa(["y", "n", "y", "n"], ["y", "y", "n", "n"]), 0.0)
        self.assertIsNone(cohen_kappa(["y"], ["y"]))

    def test_automatic_reads_both_storage_formats(self):
        auto = stats.automatic(self.a)
        self.assertEqual(auto["meaning"]["score"], 0.667)
        self.assertEqual(list(auto["meaning"]["covered"].values()), [True, True, False])
        self.assertEqual(list(auto["lexical"]["covered"].values()), [True, False, False])
        old = stats.automatic(self.b)
        self.assertNotIn("meaning", old)
        self.assertEqual(old["lexical"]["score"], 0.333)

    def test_build(self):
        self.annotate(self.a, "Sam", ["yes", "yes", "no"])
        self.annotate(self.a, "Alex", ["yes", "partly", "no"])
        self.annotate(self.b, "Sam", ["yes", "no", "no"], similar="no", coverage=None, reasons=["misses_most"])
        data = stats.build()
        self.assertEqual((data["annotations"], data["annotators"], data["outputs"]), (3, 2, 2))

        meaning, lexical = data["item_level"]
        self.assertEqual((meaning["n"], meaning["partly"], meaning["agreement"]), (5, 1, 1.0))
        # Word overlap misses item 2 that Sam called covered on output A.
        self.assertEqual((lexical["n"], lexical["missed"], lexical["extra"]), (8, 1, 0))

        agreement = data["agreement"]
        self.assertEqual((agreement["pairs"], agreement["items"], agreement["item_agreement"]), (1, 3, 0.667))
        self.assertEqual(agreement["similar_agreement"], 1.0)

        by_name = {m["name"]: m for m in data["models"]}
        self.assertEqual(by_name["Model GPT"]["annotations"], 2)
        self.assertAlmostEqual(by_name["Model GPT"]["human"], 0.584, places=3)   # mean of 0.667 and 0.5
        self.assertEqual(by_name["Model Claude"]["similar"], 0.0)
        self.assertEqual({r["key"]: r["count"] for r in data["reasons"]}["misses_most"], 1)

    def test_results_page(self):
        self.assertContains(self.client.get(reverse("annotations:results")), "No annotations yet")
        self.annotate(self.a, "Sam", ["yes", "yes", "no"])
        resp = self.client.get(reverse("annotations:results"))
        self.assertContains(resp, "Model GPT")
        self.assertContains(resp, "Clinician Match (meaning)")
        self.assertContains(self.client.get(reverse("annotations:results") + "?task=response"), "No annotations yet")

    def test_downloads(self):
        self.annotate(self.a, "Sam", ["yes", "partly", "no"])
        rows = list(csv.DictReader(io.StringIO(self.client.get(reverse("annotations:download")).content.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["items_partly"], rows[0]["auto_meaning_score"]), ("1", "0.667"))
        items = list(csv.DictReader(io.StringIO(self.client.get(reverse("annotations:download_items")).content.decode())))
        self.assertEqual([r["human_label"] for r in items], ["yes", "partly", "no"])
        self.assertEqual([r["auto_lexical_covered"] for r in items], ["yes", "no", "no"])
