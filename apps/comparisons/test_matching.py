"""Tests for clinician matching by meaning.

The alignment call is mocked exactly as the judge tests mock theirs — the
transport is the same `chat()` the app already uses for every generation, so what
is worth testing here is the prompt, the parsing, the merge with the lexical
baseline, and what happens when the call fails.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.comparisons.models import ComparisonRun, Task
from apps.comparisons.services import analysis, matcher, summary
from apps.llms.models import LLMModel
from apps.llms.services.openrouter import ChatResult, OpenRouterError

CLINICIAN = "Any blood in your stool?\nWhich medication are you taking?"
MODEL_TEXT = "1. Have you noticed any rectal bleeding?\n2. Are you on any pills at the moment?"


def reply(payload: str):
    return lambda *args, **kwargs: ChatResult(text=payload, latency_ms=5)


@override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False, MATCH_MODEL="x/small")
class ParsingTests(TestCase):
    def test_reads_the_covered_list(self):
        self.assertEqual(matcher.parse_covered('{"covered": [1, 3]}', 3), {1, 3})

    def test_ignores_prose_around_the_json(self):
        self.assertEqual(matcher.parse_covered('Sure!\n{"covered":[2]}\nHope that helps.', 2), {2})

    def test_drops_numbers_outside_the_list(self):
        self.assertEqual(matcher.parse_covered('{"covered": [0, 2, 9, "3"]}', 3), {2, 3})

    def test_empty_list_is_a_valid_answer(self):
        self.assertEqual(matcher.parse_covered('{"covered": []}', 4), set())

    def test_rubbish_raises(self):
        with self.assertRaises(ValueError):
            matcher.parse_covered("I could not tell", 2)

    def test_prompt_numbers_the_clinician_items_and_quotes_the_candidate(self):
        prompt = matcher.build_prompt(["Any blood?", "Which medication?"], "Any bleeding?", "followup")
        self.assertIn("1. Any blood?", prompt)
        self.assertIn("2. Which medication?", prompt)
        self.assertIn("Any bleeding?", prompt)
        self.assertIn("questions the clinician asked", prompt)

    def test_prompt_never_names_the_model(self):
        prompt = matcher.build_prompt(["Any blood?"], "Any bleeding?", "followup")
        for leak in ("gpt", "claude", "llama", "model a"):
            self.assertNotIn(leak, prompt.lower())


@override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False, MATCH_MODEL="x/small", MATCH_METHOD="both")
class MatchingTests(TestCase):
    def test_paraphrase_counts_where_word_overlap_fails(self):
        lexical = analysis.ground_truth_for_questions(MODEL_TEXT, CLINICIAN)
        self.assertEqual(lexical["gt_matched"], 0)          # the baseline misses both

        with patch("apps.comparisons.services.matcher.chat", side_effect=reply('{"covered":[1,2]}')):
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="both")[0]
        self.assertEqual(result["gt_method"], "meaning")
        self.assertEqual(result["gt_matched"], 2)
        self.assertEqual(result["gt_score"], 1.0)
        self.assertEqual(result["gt_missed"], [])
        self.assertEqual(result["gt_matched_lexical"], 0)   # baseline kept beside it
        self.assertGreater(result["gt_score"], result["gt_score_lexical"])

    def test_partial_coverage_lists_what_was_missed(self):
        with patch("apps.comparisons.services.matcher.chat", side_effect=reply('{"covered":[2]}')):
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="meaning")[0]
        self.assertEqual(result["gt_matched"], 1)
        self.assertEqual(result["gt_missed"], ["Any blood in your stool?"])
        self.assertEqual(result["gt_covered"], ["Which medication are you taking?"])

    def test_one_call_per_output(self):
        calls = []

        def record(model_id, system, user, temperature=None, **kwargs):
            calls.append(model_id)
            return ChatResult(text='{"covered":[1]}', latency_ms=1)

        with patch("apps.comparisons.services.matcher.chat", side_effect=record):
            analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT, 1: MODEL_TEXT, 2: MODEL_TEXT}, CLINICIAN, method="meaning")
        self.assertEqual(calls, ["x/small"] * 3)

    def test_temperature_is_zero_so_the_score_is_reproducible(self):
        seen = {}

        def record(model_id, system, user, temperature=None, **kwargs):
            seen["temperature"] = temperature
            seen["max_attempts"] = kwargs.get("max_attempts")
            return ChatResult(text='{"covered":[1]}', latency_ms=1)

        with patch("apps.comparisons.services.matcher.chat", side_effect=record):
            analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="meaning")
        self.assertEqual(seen["temperature"], 0)
        self.assertEqual(seen["max_attempts"], 1)   # fail fast to the baseline instead of retrying

    def test_response_task_aligns_sentences(self):
        clinician = "Please tell us when the bleeding started. Do not stop your medication."
        text = "Let us know when it began. Keep taking your pills until we review it."
        with patch("apps.comparisons.services.matcher.chat", side_effect=reply('{"covered":[1,2]}')):
            result = analysis.analyze(Task.RESPONSE, {0: text}, clinician, method="meaning")[0]
        self.assertEqual(result["gt_total"], 2)
        self.assertEqual(result["gt_score"], 1.0)


@override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False, MATCH_MODEL="x/small")
class FallbackTests(TestCase):
    def test_a_failed_call_falls_back_to_the_word_overlap_score(self):
        with patch("apps.comparisons.services.matcher.chat", side_effect=OpenRouterError("HTTP 429: busy")):
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="both")[0]
        self.assertEqual(result["gt_method"], "lexical")
        self.assertTrue(result["gt_fallback"])
        self.assertIn("gt_score", result)          # the card still has a number

    def test_unparseable_reply_falls_back(self):
        with patch("apps.comparisons.services.matcher.chat", side_effect=reply("no idea, sorry")):
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="both")[0]
        self.assertEqual(result["gt_method"], "lexical")

    @override_settings(OPENROUTER_API_KEY="")
    def test_no_key_means_no_call_at_all(self):
        with patch("apps.comparisons.services.matcher.chat") as called:
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="both")[0]
        called.assert_not_called()
        self.assertEqual(result["gt_method"], "lexical")

    @override_settings(LLM_MOCK_MODE=True)
    def test_mock_mode_means_no_call_at_all(self):
        with patch("apps.comparisons.services.matcher.chat") as called:
            analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="both")
        called.assert_not_called()

    def test_lexical_only_never_calls_the_api(self):
        with patch("apps.comparisons.services.matcher.chat") as called:
            result = analysis.analyze(Task.FOLLOWUP, {0: MODEL_TEXT}, CLINICIAN, method="lexical")[0]
        called.assert_not_called()
        self.assertEqual(result["gt_method"], "lexical")
        self.assertNotIn("gt_score_lexical", result)


@override_settings(OPENROUTER_API_KEY="test", LLM_MOCK_MODE=False, MATCH_MODEL="x/small", MATCH_METHOD="both")
class BothCardsTests(TestCase):
    def test_results_page_shows_the_baseline_card_beside_the_meaning_one(self):
        run = ComparisonRun.objects.create(message_text="Bleeding", task=Task.FOLLOWUP,
                                           reference_text=CLINICIAN)
        run.outputs.create(model_name="A", openrouter_id="x/a", text=MODEL_TEXT)
        with patch("apps.comparisons.services.matcher.chat", side_effect=reply('{"covered":[1,2]}')):
            from apps.comparisons.services.runner import analyse
            analyse(run)

        labels = [p["label"] for p in summary.build(run)["panels"]]
        self.assertEqual(labels[0], "Clinician Match")
        self.assertIn("Clinician Match (words)", labels)

        details = dict(summary.details_for(run, run.outputs.first()))
        self.assertIn("Clinician questions matched (by meaning)", details)
        self.assertIn("Same, by wording only (baseline)", details)


class MatcherModelTests(TestCase):
    @override_settings(MATCH_MODEL="")
    def test_picks_a_cheap_model_when_none_is_configured(self):
        LLMModel.objects.create(display_name="GPT-4o", provider="OpenAI", openrouter_id="openai/gpt-4o", order=1)
        LLMModel.objects.create(display_name="Claude Haiku 4.5", provider="Anthropic",
                                openrouter_id="anthropic/claude-haiku-4.5", order=2)
        self.assertEqual(matcher.matcher_model_id(), "anthropic/claude-haiku-4.5")

    @override_settings(MATCH_MODEL="")
    def test_no_models_means_matching_is_unavailable(self):
        self.assertEqual(matcher.matcher_model_id(), "")
        self.assertFalse(matcher.available())
