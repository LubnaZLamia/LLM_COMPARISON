from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.dataset.models import Category, PatientMessage
from apps.llms.management.commands.seed_models import Command as SeedModels
from apps.llms.management.commands.seed_models import pick_models
from apps.llms import models as marks
from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, OpenRouterError, RetryableError, chat

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
        # Anthropic: the cheap Haiku tier, not the flagship, and not an alias or batch id.
        self.assertEqual(picks["Anthropic"][0], "anthropic/claude-haiku-4.5")
        self.assertEqual(picks["Google"][0], "google/gemini-2.5-pro")
        self.assertNotIn("Meta", picks)
        self.assertNotIn("Mistral", picks)   # removed from the default line-up

    def test_haiku_falls_back_to_another_claude_when_there_is_none(self):
        cat = [{"id": "anthropic/claude-sonnet-4.5", "created": 1}, {"id": "anthropic/claude-opus-4.5", "created": 2}]
        self.assertEqual(pick_models(cat)["Anthropic"][0], "anthropic/claude-sonnet-4.5")


class ModelMarkTests(TestCase):
    """Each model gets a stable shape and a readable colour for its badge."""

    def test_known_providers_keep_their_shape(self):
        self.assertEqual(marks.mark_for("OpenAI"), "rings")
        self.assertEqual(marks.mark_for("anthropic"), "chevron")

    def test_unknown_provider_still_gets_a_stable_shape(self):
        first = marks.mark_for("Some New Lab")
        self.assertIn(first, marks.MARKS)
        self.assertEqual(first, marks.mark_for("some new lab"))

    def test_ink_is_darkened_enough_to_read(self):
        def lum(hex_color):
            r, g, b = marks._rgb(hex_color)
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255

        for color in ("#10a37f", "#f5d90a", "#2563eb", "bad-colour"):
            self.assertLessEqual(lum(marks.ink_of(color)), 0.43)

    def test_output_falls_back_when_the_model_row_is_gone(self):
        run = ComparisonRun.objects.create(message_text="hi", task=Task.FOLLOWUP)
        out = run.outputs.create(llm=None, model_name="Ghost", openrouter_id="ghost/one", text="1. Hi?")
        self.assertIn(out.mark, marks.MARKS)
        self.assertEqual(out.provider, "ghost")


class GroundTruthTests(TestCase):
    REF = ("Do you have any associated nausea?\n"
           "Have you had any associated vomiting?\n"
           "Any changes in your bowel habits?\n"
           "Have you noticed any blood in your stools?")

    def test_one_question_can_cover_two_clinician_questions(self):
        out = "1. Have you had any nausea or vomiting?\n2. Any change in your bowel habits?\n3. Do you smoke?"
        gt = analysis.ground_truth_for_questions(out, self.REF)
        self.assertEqual(gt["gt_matched"], 3)
        self.assertEqual(gt["gt_total"], 4)
        self.assertEqual(gt["gt_score"], 0.75)
        self.assertEqual(gt["gt_missed"], ["Have you noticed any blood in your stools?"])

    def test_no_reference_means_no_ground_truth_keys(self):
        res = analysis.analyze(Task.FOLLOWUP, {0: "1. When did it start?"})[0]
        self.assertNotIn("gt_score", res)

    def test_response_coverage(self):
        ref = "Please call the office to schedule an appointment. Avoid fatty foods until then."
        good = analysis.ground_truth_for_response("Please call our office to schedule an appointment. "
                                                  "Try to avoid fatty foods until then.", ref)
        self.assertEqual(good["gt_score"], 1.0)
        poor = analysis.ground_truth_for_response("Drink plenty of water and rest.", ref)
        self.assertEqual(poor["gt_score"], 0.0)


class RetryTests(TestCase):
    """HTTP 429 ("provider busy") is the failure we kept seeing; it must be retried, not shown."""

    @override_settings(OPENROUTER_API_KEY="test", OPENROUTER_MAX_ATTEMPTS=3, OPENROUTER_RETRY_BACKOFF=0)
    def test_retries_then_succeeds(self):
        calls = []

        def flaky(model_id, system, user, temperature):
            calls.append(model_id)
            if len(calls) < 3:
                raise RetryableError("HTTP 429: rate limited or provider busy")
            return ChatResult(text="ok", latency_ms=5)

        with patch("apps.llms.services.openrouter._once", side_effect=flaky):
            result = chat("mistralai/mistral-large", "sys", "user")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(calls), 3)

    @override_settings(OPENROUTER_API_KEY="test", OPENROUTER_MAX_ATTEMPTS=2, OPENROUTER_RETRY_BACKOFF=0)
    def test_gives_up_with_clear_message(self):
        with patch("apps.llms.services.openrouter._once", side_effect=RetryableError("HTTP 429: provider busy")):
            with self.assertRaises(OpenRouterError) as ctx:
                chat("mistralai/mistral-large", "sys", "user")
        self.assertIn("gave up after 2 attempts", str(ctx.exception))

    @override_settings(OPENROUTER_API_KEY="test", OPENROUTER_MAX_ATTEMPTS=3, OPENROUTER_RETRY_BACKOFF=0)
    def test_permanent_errors_are_not_retried(self):
        calls = []

        def dead(model_id, system, user, temperature):
            calls.append(model_id)
            raise OpenRouterError("HTTP 404: No endpoints found")

        with patch("apps.llms.services.openrouter._once", side_effect=dead):
            with self.assertRaises(OpenRouterError):
                chat("x/gone", "sys", "user")
        self.assertEqual(len(calls), 1)


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

    def test_dropped_providers_are_hidden_on_the_next_start(self):
        """Mistral was taken out of the line-up: --repair hides it but keeps the row,
        so its past runs still make sense."""
        gone = LLMModel.objects.create(display_name="Mistral Large", provider="Mistral",
                                       openrouter_id="mistralai/mistral-large", order=5)
        kept = LLMModel.objects.create(display_name="GPT-4o", provider="OpenAI",
                                       openrouter_id="openai/gpt-4o", order=1)
        SeedModels().drop_unconfigured()
        gone.refresh_from_db(); kept.refresh_from_db()
        self.assertFalse(gone.is_active)
        self.assertTrue(kept.is_active)
        self.assertTrue(LLMModel.objects.filter(pk=gone.pk).exists())  # history intact


class CompareViewTests(TestCase):
    def setUp(self):
        # Every message offered in the list carries ground truth for at least one task.
        self.msg = PatientMessage.objects.create(
            text="I feel dizzy after my medication.", category=Category.MEDICATION,
            reference_followup="Which medication are you taking?\nWhen did the dizziness start?",
        )
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
             patch("apps.comparisons.services.matcher.chat", side_effect=fake_chat), \
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
    def test_ground_truth_card_and_ehr_are_used(self):
        msg = PatientMessage.objects.create(
            text="Stomach pains and gas.", category=Category.SYMPTOMS,
            ehr_summary="###Demographics### Age: 35-40  Male",
            reference_followup="Do you have any associated nausea?\nAny changes in your bowel habits?",
        )
        self.client.post(reverse("comparisons:compare"), {
            "source": "example", "message": msg.pk, "task": Task.FOLLOWUP, "models": [self.a.pk, self.b.pk],
        })
        run = ComparisonRun.objects.get()
        self.assertEqual(run.ehr_summary, msg.ehr_summary)      # chart snapshot kept with the run
        self.assertTrue(all("gt_score" in o.analysis for o in run.outputs.all()))
        page = self.client.get(reverse("comparisons:run", args=[run.pk]))
        self.assertContains(page, "Clinician Match")
        self.assertContains(page, "Chart summary sent to the models")
        self.assertContains(self.client.get(reverse("comparisons:download", args=[run.pk])), "ground_truth_score")

    @override_settings(LLM_MOCK_MODE=True)
    def test_response_ground_truth_and_missing_case(self):
        with_reply = PatientMessage.objects.create(
            text="Cough for two weeks.", category=Category.SYMPTOMS,
            reference_response="Please call the office to schedule an appointment. Avoid smoke until then.",
        )
        self.client.post(reverse("comparisons:compare"), {
            "source": "example", "message": with_reply.pk, "task": Task.RESPONSE, "models": [self.a.pk],
        })
        run = ComparisonRun.objects.latest("id")
        self.assertTrue(all("gt_score" in o.analysis for o in run.outputs.all()))
        self.assertContains(self.client.get(reverse("comparisons:run", args=[run.pk])), "Clinician Match")

    @override_settings(LLM_MOCK_MODE=True)
    def test_each_task_only_accepts_its_own_dataset(self):
        """A message with questions but no clinician reply can't be run as a reply task."""
        questions_only = PatientMessage.objects.create(
            text="Knee pain after running.", category=Category.SYMPTOMS,
            reference_followup="How long has it hurt?\nAny swelling?",
        )
        before = ComparisonRun.objects.count()
        page = self.client.post(reverse("comparisons:compare"), {
            "source": "example", "message": questions_only.pk, "task": Task.RESPONSE, "models": [self.a.pk],
        })
        self.assertContains(page, "has no clinician reply")
        self.assertEqual(ComparisonRun.objects.count(), before)  # nothing was run

        # The same message is fine for the task the clinician did answer.
        self.client.post(reverse("comparisons:compare"), {
            "source": "example", "message": questions_only.pk, "task": Task.FOLLOWUP, "models": [self.a.pk],
        })
        self.assertEqual(ComparisonRun.objects.count(), before + 1)

    @override_settings(LLM_MOCK_MODE=True)
    def test_list_is_scoped_to_the_selected_task(self):
        PatientMessage.objects.create(text="Cough.", category=Category.SYMPTOMS,
                                      reference_response="Please call the office.")
        PatientMessage.objects.create(text="No ground truth at all.", category=Category.OTHER)
        page = self.client.get(reverse("comparisons:compare")).content.decode()
        # Each message is tagged with the tasks it can be scored for; the page never
        # spells any of this out, it just filters the list.
        self.assertIn('data-gt-followup="1" data-gt-response="0"', page)   # self.msg
        self.assertIn('data-gt-followup="0" data-gt-response="1"', page)   # the cough message
        self.assertNotIn("No ground truth at all", page)                   # never offered
        for leak in ("clinician questions", "clinician reply", "ground truth", "dataset for"):
            self.assertNotIn(leak, page.lower())

    @override_settings(LLM_MOCK_MODE=True)
    def test_your_own_message_says_why_there_is_no_ground_truth(self):
        self.client.post(reverse("comparisons:compare"), {
            "source": "custom", "custom_text": "My elbow aches.", "custom_category": Category.SYMPTOMS,
            "task": Task.RESPONSE, "models": [self.a.pk],
        })
        page = self.client.get(reverse("comparisons:run", args=[ComparisonRun.objects.latest("id").pk]))
        self.assertContains(page, "Clinician Match")
        self.assertContains(page, "You wrote this message yourself")

    @override_settings(LLM_MOCK_MODE=True)
    def test_ehr_reaches_the_prompt(self):
        from apps.comparisons.services.prompts import build_prompts
        _, user = build_prompts(Task.FOLLOWUP, "My knee hurts.", "Age: 35-40, Obesity")
        self.assertIn("Obesity", user)
        self.assertIn("My knee hurts.", user)

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

    @override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False)
    def test_retry_failed_button_reruns_only_failures(self):
        def first_pass(model_id, system, user, temperature=None):
            if model_id == "y/b":
                raise OpenRouterError("HTTP 429: provider busy (gave up after 3 attempts)")
            return ChatResult(text="1. Which medication are you taking?\n2. Any fever?", latency_ms=10)

        with patch("apps.comparisons.services.runner.chat", side_effect=first_pass), \
             patch("apps.comparisons.services.matcher.chat", side_effect=first_pass):
            self.client.post(reverse("comparisons:compare"), {
                "source": "example", "message": self.msg.pk, "task": Task.FOLLOWUP,
                "models": [self.a.pk, self.b.pk],
            })
        run = ComparisonRun.objects.get()
        self.assertTrue(any(o.error for o in run.outputs.all()))
        self.assertContains(self.client.get(reverse("comparisons:run", args=[run.pk])), "Retry failed")

        calls = []

        def second_pass(model_id, system, user, temperature=None):
            calls.append(model_id)
            return ChatResult(text="1. When did it start?\n2. Any fever?", latency_ms=8)

        with patch("apps.comparisons.services.runner.chat", side_effect=second_pass), \
             patch("apps.comparisons.services.matcher.chat", side_effect=second_pass):
            self.client.post(reverse("comparisons:retry", args=[run.pk]))
        self.assertEqual(calls, ["y/b"])  # only the failed model was called again
        self.assertEqual(run.outputs.count(), 2)
        self.assertFalse(any(o.error for o in run.outputs.all()))
        # agreement is recomputed across both answers
        self.assertTrue(all(o.analysis.get("agreement") is not None for o in run.outputs.all()))

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
