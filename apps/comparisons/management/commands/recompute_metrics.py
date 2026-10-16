"""Re-score past runs with a chosen matching method, without calling any model again.

The outputs are already stored, so re-scoring them with a different matching
method is a recompute, not a regeneration. This is what makes the before/after comparison
possible on runs you have already done.

    python manage.py recompute_metrics                 # every run, using MATCH_METHOD
    python manage.py recompute_metrics --method both
    python manage.py recompute_metrics --runs 27 30
    python manage.py recompute_metrics --dry-run
"""
from django.core.management.base import BaseCommand

from apps.comparisons.models import ComparisonRun
from apps.comparisons.services import analysis


class Command(BaseCommand):
    help = "Recompute the stored analysis for past runs with the current (or a given) matching method."

    def add_arguments(self, parser):
        parser.add_argument("--method", choices=["lexical", "meaning", "both"], default=None)
        parser.add_argument("--runs", nargs="*", type=int, default=None, help="Run ids; default is all of them.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, write nothing.")

    def handle(self, *args, **opts):
        runs = ComparisonRun.objects.all().order_by("id")
        if opts["runs"]:
            runs = runs.filter(pk__in=opts["runs"])

        changed = unchanged = 0
        for run in runs:
            outputs = list(run.outputs.all())
            texts = {i: o.text for i, o in enumerate(outputs) if not o.error and o.text}
            if not texts:
                continue
            results = analysis.analyze(run.task, texts, run.reference_text, method=opts["method"])
            moved = []
            for i, out in enumerate(outputs):
                data = results.get(i)
                if data is None or out.analysis == data:
                    continue
                before = (out.analysis or {}).get("gt_score")
                after = data.get("gt_score")
                if before is not None and after is not None and before != after:
                    moved.append(f"{out.model_name} {before:.0%}→{after:.0%}")
                if not opts["dry_run"]:
                    out.analysis = data
                    out.save(update_fields=["analysis"])
                changed += 1
            if moved:
                self.stdout.write(f"  run {run.pk} ({run.get_task_display()}): " + ", ".join(moved))
            if not moved:
                unchanged += 1

        verb = "Would update" if opts["dry_run"] else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {changed} output(s) across {runs.count()} run(s); {unchanged} run(s) unchanged."))
