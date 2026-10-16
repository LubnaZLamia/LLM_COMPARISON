"""Prove the meaning-based matching works, against the real API, in ten seconds.

Eight cases with a known right answer: four where the candidate covers the
clinician's question in different words, and four where it does not. The
word-overlap baseline is scored on the same cases, so the output is a direct
before/after rather than a claim.

    python manage.py check_matching
    python manage.py check_matching --model anthropic/claude-haiku-4.5

Exit status is 0 when the matcher gets at least 7 of 8 right, 1 otherwise, so a
bad model or a broken key fails loudly instead of quietly degrading a metric.
"""
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.comparisons.services import matcher
from apps.comparisons.services.analysis import ground_truth_for_questions

# (clinician question, candidate text, should it count as covered?)
CASES = [
    ("Any blood in your stool?", "Have you noticed any rectal bleeding?", True),
    ("Which medication are you taking?", "Are you on any pills or tablets at the moment?", True),
    ("When did the pain start?", "How long has this been going on for?", True),
    ("Any changes in your bowel movements?",
     "Have you had any diarrhoea or constipation recently?", True),
    ("Any blood in your stool?", "How severe is the pain on a scale of 0 to 10?", False),
    ("When did the pain start?", "Do you have a family history of heart disease?", False),
    ("Which medication are you taking?", "Are you drinking enough water each day?", False),
    # The hard one: same words, opposite meaning. Word overlap always gets this wrong.
    ("Should the patient come in today?", "There is no need to come in today.", False),
]


class Command(BaseCommand):
    help = "Check the meaning-based clinician matching against the real API."

    def add_arguments(self, parser):
        parser.add_argument("--model", default="", help="Override MATCH_MODEL for this check.")

    def handle(self, *args, **opts):
        if settings.LLM_MOCK_MODE:
            self.stderr.write(self.style.ERROR(
                "LLM_MOCK_MODE is true, so no real call would be made. Set it to false in .env."))
            sys.exit(1)
        if not settings.OPENROUTER_API_KEY:
            self.stderr.write(self.style.ERROR("No OPENROUTER_API_KEY in .env."))
            sys.exit(1)

        model_id = opts["model"] or matcher.matcher_model_id()
        if not model_id:
            self.stderr.write(self.style.ERROR(
                "No model to match with. Add one in /admin/llms/llmmodel/ or set MATCH_MODEL in .env."))
            sys.exit(1)

        self.stdout.write(f"Matching with {model_id}\n")
        self.stdout.write(f"  {'meaning':>8}  {'words':>7}   case")
        self.stdout.write("  " + "-" * 76)

        meaning_right = words_right = 0
        failures = []
        for clinician, candidate, expected in CASES:
            result = matcher.match_one(model_id, [clinician], candidate, "followup")
            if result.error:
                self.stderr.write(self.style.ERROR(f"  call failed: {result.error}"))
                sys.exit(1)
            got = result.matched == 1

            lexical = ground_truth_for_questions(candidate, clinician)
            got_lexical = bool(lexical.get("gt_matched"))

            meaning_right += got == expected
            words_right += got_lexical == expected
            if got != expected:
                failures.append((clinician, candidate, expected))

            self.stdout.write(
                f"  {self._mark(got == expected):>8}  {self._mark(got_lexical == expected):>7}   "
                f"{'covers' if expected else 'does not'}: \"{candidate[:46]}\"")

        total = len(CASES)
        self.stdout.write("")
        self.stdout.write(f"  by meaning: {meaning_right}/{total}")
        self.stdout.write(f"  by words:   {words_right}/{total}   (the baseline it replaces)")

        if failures:
            self.stdout.write("\n  Got wrong:")
            for clinician, candidate, expected in failures:
                self.stdout.write(f"    expected {'covered' if expected else 'not covered'}: "
                                  f"\"{clinician}\" vs \"{candidate}\"")

        self.stdout.write("")
        if meaning_right >= total - 1:
            self.stdout.write(self.style.SUCCESS(
                "Matching works. Nothing else to set up — run.bat already uses it."))
            if meaning_right > words_right:
                self.stdout.write(f"It beat the word-overlap baseline by {meaning_right - words_right} "
                                  f"case(s) on this check.")
        else:
            self.stderr.write(self.style.ERROR(
                f"Only {meaning_right}/{total}. Try a stronger model with --model, or set "
                f"MATCH_METHOD=lexical in .env to keep the baseline until it is sorted."))
            sys.exit(1)

    @staticmethod
    def _mark(ok: bool) -> str:
        return "ok" if ok else "MISS"
