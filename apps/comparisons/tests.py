from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.dataset.models import Category, PatientMessage
from apps.llms.management.commands.seed_models import Command as SeedModels
from apps.llms.management.commands.seed_models import pick_models
from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, OpenRouterError

from .models import ComparisonRun, Task
from .services import analysis, judge, metrics


class AnalysisTests(TestCase):
    def test_followup_analysis(self):
        res = analysis.analyze(Task.FOLLOWUP, {
            0: "1. When did the dizziness start?\n2. Which medication are you taking?\n3. When did your dizziness start?",
            1: "1. What medication are you taking right now?\n2. Do you have chest pain?",
        })
        a, b = res[0], res[1]
        self.assertEqual(a["question_count"], 3)
        self.assertEqual(len(a["repeated"]), 1)
        self.assertIn("Medications", a["topics"])
        self.assertAlmostEqual(a["agreement"], 0.33, places=2)
        self.assertIn("Red flags", b["topics"])
        self.assertEqual(b["unique"], ["Do you have chest pain?"])

    def test_questions_without_list(self):
        self.assertEqual(len(analysis.extract_questions("When did it start? Is it severe? Thanks.")), 2)

    def test_response_analysis(self):
        text = ("I'm sorry you're feeling dizzy. Please call the office so your provider can review your medication. "
                "If you faint or have chest pain, call 911 right away.")
        r = analysis.analyze(Task.RESPONSE, {0: text})[0]
        self.assertEqual(set(r["elements"]), set(analysis.ELEMENTS))
        self.assertTrue(0 < r["grade_level"] < 12)
        self.assertGreater(r["word_count"], 20)

    def test_reference_metrics_still_work(self):
        ref = "When did the dizziness start?\nWhich medication are you taking?"
        self.assertEqual(metrics.score("1. When did your dizziness start?\n2. Do you smoke?", ref).reference_coverage, 0.5)

    def test_judge_parsing(self):
        raw = 'Sure:\n```json\n{"scores": {"relevance": 5, "comprehensiveness": "4", "safety": 9, "clarity": 3}, "rationale": "Good."}\n```'
        out = judge.parse_scores(raw, Task.FOLLOWUP)
        self.assertEqual(out["scores"]["safety"], 5)
        self.assertEqual(out["overall"], 4.25)

    def test_model_picking(self):
        cat = [{"id": i, "created": n} for n, i in enumerate([
            "openai/gpt-4o", "openai/gpt-5", "openai/gpt-5-mini", "openai/gpt-5:batch",
            "anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.5",
            "google/gemini-2.5-flash-lite", "google/gemini-2.5-pro",
        ])]
        picks = pick_models(cat)
        self.assertEqual(picks["OpenAI"][0], "openai/gpt-5")
        self.assertEqual(picks["Anthropic"][0], "anthropic/claude-sonnet-4.5")
        self.assertEqual(picks["Google"][0], "google/gemini-2.5-pro")
        self.assertNotIn("Meta", picks)


class RepairModelsTests(TestCase):
    def test_retired_ids_are_replaced(self):
        old = LLMModel.objects.create(display_name="Claude 3.5 Sonnet", provider="Anthropic",
                                      openrouter_id="anthropic/claude-3.5-sonnet", order=1)
        orphan = LLMModel.objects.create(display_name="Gone", provider="Nobody",
                                         openrouter_id="nobody/model-1", order=2)
        catalogue = [{"id": "anthropic/claude-sonnet-4.5", "name": "Anthropic: Claude Sonnet 4.5", "created": 10}]
        cmd = SeedModels()
        cmd.repair(catalogue, pick_models(catalogue))
        old.refresh_from_db(); orphan.refresh_from_db()
        self.assertEqual(old.openrouter_id, "anthropic/claude-sonnet-4.5")
        self.assertEqual(old.display_name, "Claude Sonnet 4.5")
        self.assertFalse(orphan.is_active)


class CompareViewTests(TestCase):
    def setUp(self):
        self.msg = PatientMessage.objects.create(text="I feel dizzy after my medication.", category=Category.MEDICATION)
        self.a = LLMModel.objects.create(display_name="Model A", provider="X", openrouter_id="x/a", order=1, is_judge=True)
        self.b = LLMModel.objects.create(display_name="Model B", provider="Y", openrouter_id="y/b", order=2)
        self.c = LLMModel.objects.create(display_name="Model C", provider="Z", openrouter_id="z/c", order=3)

    def test_get_compare_page(self):
        r = self.client.get(reverse("comparisons:compare"))
        self.assertContains(r, "Generate Outputs")
        self.assertContains(r, "Score with judge model")

    @override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False, JUDGE_MODEL="")
    def test_run_with_failure_and_judge(self):
        def fake_chat(model_id, system, user, temperature=None):
            if "evaluating" in system:
                return ChatResult(text='{"scores": {"relevance": 4, "comprehensiveness": 4, "safety": 5, "clarity": 3}, "rationale": "ok"}', latency_ms=5)
            if model_id == "z/c":
                raise OpenRouterError("HTTP 404: model not found")
            if model_id == "x/a":
                return ChatResult(text="1. Which medication are you taking?\n2. Any fever?\n3. Any chest pain?", latency_ms=12)
            return ChatResult(text="1. What medication are you taking?", latency_ms=9)

        with patch("apps.llms.services.openrouter.chat", side_effect=fake_chat), \
             patch("apps.comparisons.services.runner.chat", side_effect=fake_chat), \
             patch("apps.comparisons.services.judge.chat", side_effect=fake_chat):
            r = self.client.post(reverse("comparisons:compare"), {
                "source": "example", "message": self.msg.pk, "task": Task.FOLLOWUP,
                "models": [self.a.pk, self.b.pk, self.c.pk], "use_judge": "on",
            })
        run = ComparisonRun.objects.get()
        self.assertEqual(run.judge_model, "x/a")
        a_out, b_out, c_out = run.outputs.order_by("llm__order")
        self.assertEqual(a_out.analysis["question_count"], 3)
        self.assertEqual(b_out.analysis["agreement"], 1.0)
        self.assertEqual(a_out.judge["overall"], 4.0)
        self.assertIn("404", c_out.error)
        self.assertEqual(c_out.analysis, {})

        page = self.client.get(reverse("comparisons:run", args=[run.pk]))
        self.assertContains(page, "Asked the most")
        self.assertContains(page, "Topics Covered")
        self.assertContains(page, "Judge Score")
        self.assertContains(page, "Request failed")
        self.assertNotContains(page, "Reference Coverage")
        self.assertContains(self.client.get(reverse("comparisons:download", args=[run.pk])), "Model A")

    @override_settings(LLM_MOCK_MODE=True)
    def test_mock_response_run(self):
        self.client.post(reverse("comparisons:compare"), {
            "source": "custom", "custom_text": "My knee hurts.", "custom_category": Category.SYMPTOMS,
            "save_to_dataset": "on", "task": Task.RESPONSE, "models": [self.a.pk, self.b.pk], "use_judge": "on",
        })
        self.assertTrue(PatientMessage.objects.filter(text="My knee hurts.").exists())
        run = ComparisonRun.objects.get()
        page = self.client.get(reverse("comparisons:run", args=[run.pk]) + "?view=table")
        self.assertContains(page, "Easiest to read")
        self.assertContains(page, "Key elements")

    def test_judge_is_off_by_default(self):
        r = self.client.get(reverse("comparisons:compare"))
        self.assertNotContains(r, 'name="use_judge" checked')

    def test_validation_requires_model(self):
        r = self.client.post(reverse("comparisons:compare"), {"source": "example", "message": self.msg.pk, "task": Task.FOLLOWUP})
        self.assertContains(r, "Select at least one model.")


class DatasetTests(TestCase):
    def test_crud_and_csv(self):
        r = self.client.post(reverse("dataset:create"), {"text": "Hello", "category": Category.OTHER, "is_featured": "on"})
        self.assertRedirects(r, reverse("dataset:list"))
        self.assertContains(self.client.get(reverse("dataset:export")), "Hello")
