"""import_exports: shared runs from exports/*.csv become annotatable items. No API calls."""
import csv
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from apps.comparisons.models import ComparisonRun, ModelOutput
from apps.ratings.models import HumanRating

from .models import PeerAnnotation
from .services import queue, sharing, stats

EXPORTS = Path(settings.BASE_DIR) / "exports"


def run_import(folder=EXPORTS) -> str:
    out = StringIO()
    call_command("import_exports", dir=str(folder), stdout=out)
    return out.getvalue()


def rows(name):
    with (EXPORTS / f"{name}.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class ImportExportsTests(TestCase):
    def test_shared_exports_load_and_are_annotatable(self):
        if not (EXPORTS / "runs.csv").exists():
            self.skipTest("no exports/ folder in this checkout")
        runs, outputs = rows("runs"), rows("outputs")
        run_import()
        self.assertEqual(ComparisonRun.objects.count(), len(runs))
        self.assertEqual(ModelOutput.objects.count(), len(outputs))
        usable = [o for o in outputs if not o["error"] and o["output_text"].strip() and o["clinician_reference"].strip()]
        self.assertEqual(queue.eligible().count(), len(usable))
        # Every annotatable output can be checked against at least the word-overlap matcher.
        self.assertTrue(all("lexical" in stats.automatic(o) for o in queue.eligible()))

    def test_ids_kept_and_second_import_adds_nothing(self):
        if not (EXPORTS / "runs.csv").exists():
            self.skipTest("no exports/ folder in this checkout")
        run_import()
        first = rows("runs")[0]
        self.assertEqual(ComparisonRun.objects.get(pk=int(first["run_id"])).message_text, first["message_text"])
        counts = (ComparisonRun.objects.count(), ModelOutput.objects.count(), HumanRating.objects.count())
        self.assertIn("0 new run(s)", run_import())
        self.assertEqual((ComparisonRun.objects.count(), ModelOutput.objects.count(), HumanRating.objects.count()), counts)


class SmallExport(TestCase):
    """A hand-made export, to pin down the column mapping."""

    RUNS = [{"run_id": "5", "created_at": "2026-10-01 10:00:00.000001", "task": "followup", "category": "symptoms",
             "source_message_id": "", "models_run": "2", "models_failed": "0", "judge_model": "", "mock_mode": "False",
             "message_text": "My stomach hurts.", "ehr_summary": "",
             "clinician_reference": "Any fever?\nWhen did it start?"}]
    OUT_BASE = {"run_id": "5", "created_at": "", "task": "followup", "category": "symptoms", "source_message_id": "",
                "openrouter_id": "x/y", "error": "", "truncated": "False", "attempts": "1", "latency_ms": "900",
                "prompt_tokens": "", "completion_tokens": "", "message_text": "My stomach hurts.", "ehr_summary": "",
                "clinician_reference": "Any fever?\nWhen did it start?"}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        meaning = {**self.OUT_BASE, "output_id": "9", "model_name": "A", "output_text": "1. Do you have a temperature?",
                   "match_method": "meaning", "match_score": "0.5", "match_matched": "1", "match_total": "2",
                   "match_missed": "When did it start?", "match_covered": "Any fever?",
                   "match_score_lexical": "0.0", "match_matched_lexical": "0", "match_total_lexical": "2",
                   "match_missed_lexical": "Any fever? | When did it start?", "judge_overall": "4.5",
                   "judge_relevance": "5", "judge_clarity": "4", "judge_rationale": "Fine."}
        unscored = {**self.OUT_BASE, "output_id": "10", "model_name": "B", "output_text": "1. Any fever?"}
        self.write("runs", self.RUNS)
        self.write("outputs", [meaning, unscored])
        self.write("ratings", [{"rating_id": "1", "run_id": "5", "output_id": "9", "task": "followup", "model_name": "A",
                                "blind_label": "Model A", "rater": "LR", "fluency": "4", "coherence": "4",
                                "relevance": "5", "factuality": "4", "completeness": "3", "safety": "5",
                                "comment": "", "created_at": "2026-10-02 09:00:00"}])

    def write(self, name, data):
        keys = sorted({k for row in data for k in row})
        with (self.dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys)
            writer.writeheader()
            writer.writerows(data)


class SmallExportTests(SmallExport):
    def test_columns_map_to_analysis_judge_and_ratings(self):
        run_import(self.dir)
        a, b = ModelOutput.objects.get(model_name="A"), ModelOutput.objects.get(model_name="B")
        self.assertEqual(a.pk, 9)
        self.assertEqual(a.analysis["gt_covered"], ["Any fever?"])
        self.assertEqual(a.analysis["gt_missed_lexical"], ["Any fever?", "When did it start?"])
        self.assertEqual(a.judge["scores"], {"relevance": 5, "clarity": 4})
        auto = stats.automatic(a)
        self.assertEqual(list(auto["meaning"]["covered"].values()), [True, False])
        # No score in the file: the word-overlap score is computed on import.
        self.assertEqual((b.analysis["gt_method"], b.analysis["gt_score"]), ("lexical", 0.5))
        rating = HumanRating.objects.get()
        self.assertEqual((rating.output, rating.rater, rating.safety), (a, "LR", 5))

    def test_taken_ids_are_not_overwritten(self):
        other = ComparisonRun.objects.create(pk=5, message_text="Someone else's run", task="followup")
        run_import(self.dir)
        other.refresh_from_db()
        self.assertEqual(other.message_text, "Someone else's run")
        imported = ComparisonRun.objects.get(message_text="My stomach hurts.")
        self.assertNotEqual(imported.pk, 5)
        self.assertEqual(imported.outputs.count(), 2)
        self.assertIn("0 new run(s)", run_import(self.dir))


class SharedAnnotationTests(SmallExport):
    """Annotations written on one machine load on another, whatever the ids are there."""

    def setUp(self):
        super().setUp()
        run_import(self.dir)
        self.output = ModelOutput.objects.get(model_name="A")
        self.annotation = PeerAnnotation.objects.create(
            output=self.output, annotator="Sam Kim", reference_items=["Any fever?", "When did it start?"],
            item_labels=["yes", "no"], item_score=0.5, candidate_units=["Do you have a temperature?"],
            item_links=[[0], []], unit_score=1.0, similar=True, coverage=0, extra="none", confidence="sure",
            comment="Line one, \"quoted\"\nline two", seconds=42)

    def export(self, **kwargs):
        out = StringIO()
        call_command("export_annotations", dir=str(self.dir), stdout=out, **kwargs)
        return out.getvalue()

    def rows(self):
        with (self.dir / "annotations" / "sam-kim.csv").open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def rewrite(self, rows):
        with (self.dir / "annotations" / "sam-kim.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=sharing.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_round_trip_keeps_every_field(self):
        self.export()
        before = PeerAnnotation.objects.values().get()
        PeerAnnotation.objects.all().delete()
        self.assertIn("1 annotation(s) added", run_import(self.dir))
        after = PeerAnnotation.objects.values().get()
        before.pop("id"), after.pop("id")
        self.assertEqual(after, before)

    def test_found_by_fingerprint_when_the_id_differs_here(self):
        self.export()
        PeerAnnotation.objects.all().delete()
        ComparisonRun.objects.all().delete()
        ComparisonRun.objects.create(pk=5, message_text="Someone else's run", task="followup")
        blocker = ModelOutput.objects.create(pk=9, run=ComparisonRun.objects.get(pk=5), model_name="Z", text="Other")
        run_import(self.dir)
        annotation = PeerAnnotation.objects.get()
        self.assertNotEqual(annotation.output, blocker)
        self.assertEqual(annotation.output.text, "1. Do you have a temperature?")

    def test_loading_twice_changes_nothing(self):
        self.export()
        PeerAnnotation.objects.all().delete()
        run_import(self.dir)
        stamp = PeerAnnotation.objects.get().updated_at
        self.assertIn("0 annotation(s) added, 0 updated", run_import(self.dir))
        self.assertEqual(PeerAnnotation.objects.get().updated_at, stamp)

    def test_newer_version_wins_either_way(self):
        self.export()
        row = self.rows()[0]
        self.rewrite([{**row, "item_labels": '["no", "no"]', "item_score": "0.0",
                       "updated_at": "2030-01-01T00:00:00+00:00"}])
        self.assertIn("1 updated", run_import(self.dir))
        self.assertEqual(PeerAnnotation.objects.get().item_score, 0.0)
        self.rewrite([{**row, "item_labels": '["yes", "yes"]', "item_score": "1.0",
                       "updated_at": "2020-01-01T00:00:00+00:00"}])
        self.assertIn("0 updated", run_import(self.dir))
        self.assertEqual(PeerAnnotation.objects.get().item_score, 0.0)

    def test_same_person_in_any_case_is_one_annotation(self):
        self.export()
        row = self.rows()[0]
        self.rewrite([{**row, "annotator": "sam kim", "updated_at": "2030-01-01T00:00:00+00:00"}])
        run_import(self.dir)
        self.assertEqual(PeerAnnotation.objects.count(), 1)

    def test_damaged_rows_and_unknown_outputs_are_skipped_not_fatal(self):
        self.export()
        row = self.rows()[0]
        self.rewrite([{**row, "output_key": "nothing-like-this"}, {**row, "item_labels": "not json"},
                      {**row, "item_labels": '["yes"]'}, {**row, "updated_at": "someday"}])
        PeerAnnotation.objects.all().delete()
        out = run_import(self.dir)
        self.assertIn("Skipped 1 annotation(s) for outputs not in this database and 3 damaged row(s)", out)
        self.assertFalse(PeerAnnotation.objects.exists())

    def test_exporting_unchanged_work_gives_the_same_file(self):
        self.export()
        path = self.dir / "annotations" / "sam-kim.csv"
        first = path.read_bytes()
        self.export()
        self.assertEqual(path.read_bytes(), first)

    def test_one_file_each_and_the_annotator_filter(self):
        PeerAnnotation.objects.create(output=self.output, annotator="Alex", reference_items=[], item_labels=[])
        self.assertIn("2 annotation(s) to 2 file(s)", self.export())
        self.assertEqual(sorted(p.name for p in (self.dir / "annotations").glob("*.csv")), ["alex.csv", "sam-kim.csv"])
        shutil.rmtree(self.dir / "annotations")
        self.assertIn("1 annotation(s) to 1 file(s)", self.export(annotator="sam kim"))
        self.assertEqual([p.name for p in (self.dir / "annotations").glob("*.csv")], ["sam-kim.csv"])

    def test_names_that_make_no_slug_still_get_a_file(self):
        self.assertRegex(sharing.file_name("李明"), r"^[0-9a-f]{12}\.csv$")

    def test_a_second_annotator_is_offered_shared_work_first(self):
        self.assertEqual(queue.next_for("Alex"), self.output)
