"""Load shared comparison runs from the exports/ folder into this database.

Model outputs live in each person's own db.sqlite3, which git does not track.
To give everyone the same outputs to annotate, one person exports their runs to
exports/runs.csv, exports/outputs.csv and exports/ratings.csv, and everyone else
loads them with:

    python manage.py import_exports                 # reads ./exports
    python manage.py import_exports --dir other/    # or another folder

Safe to run again (run.bat and run.sh do, on every start):
  * a run whose created_at, task and message already exist is skipped, so the
    person who exported, or anyone who imported before, gets no duplicates;
  * run, output and rating ids from the files are kept when they are free, so
    links such as /runs/27/ point at the same run on every machine;
  * outputs with no stored clinician-match score get the word-overlap score,
    computed here for free. Nothing calls a model.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.comparisons.models import ComparisonRun, ModelOutput, Task
from apps.comparisons.services.analysis import ground_truth_for_questions, ground_truth_for_response
from apps.dataset.models import PatientMessage
from apps.llms.models import LLMModel
from apps.ratings.models import DIMENSIONS, HumanRating

SEP = " | "   # how the export joins lists into one cell
JUDGE_KEYS = ("relevance", "comprehensiveness", "safety", "clarity", "empathy")


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def when(value: str):
    """Exported timestamps are UTC without an offset, as Django stores them."""
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def number(value: str, cast=float):
    try:
        return cast(value) if value not in ("", None) else None
    except ValueError:
        return None


def items(value: str) -> list[str]:
    return [part.strip() for part in value.split(SEP) if part.strip()] if value else []


def analysis_from(row: dict) -> dict:
    """Rebuild the stored analysis dict from the export columns."""
    a = {}
    if row.get("match_score"):
        a.update({
            "gt_score": number(row["match_score"]),
            "gt_matched": number(row.get("match_matched"), int),
            "gt_total": number(row.get("match_total"), int),
            "gt_missed": items(row.get("match_missed", "")),
        })
        if row.get("match_method"):
            a["gt_method"] = row["match_method"]
        if row.get("match_method") == "meaning":
            a["gt_covered"] = items(row.get("match_covered", ""))
    if row.get("match_score_lexical"):
        a.update({
            "gt_score_lexical": number(row["match_score_lexical"]),
            "gt_matched_lexical": number(row.get("match_matched_lexical"), int),
            "gt_total_lexical": number(row.get("match_total_lexical"), int),
            "gt_missed_lexical": items(row.get("match_missed_lexical", "")),
        })
    for key, column, cast in (("question_count", "question_count", int), ("agreement", "agreement", float),
                              ("word_count", "word_count", int), ("grade_level", "grade_level", float)):
        value = number(row.get(column), cast)
        if value is not None:
            a[key] = value
    if row.get("topics") or row.get("topic_count"):
        a["topics"] = items(row.get("topics", ""))
    if row.get("repeated_questions") or row.get("question_count"):
        a["repeated"] = items(row.get("repeated_questions", ""))
    if row.get("key_elements") or row.get("key_element_count"):
        a["elements"] = items(row.get("key_elements", ""))
    return {k: v for k, v in a.items() if v is not None}


def judge_from(row: dict) -> dict:
    if not row.get("judge_overall") and not row.get("judge_rationale"):
        return {}
    scores = {k: number(row.get(f"judge_{k}"), int) for k in JUDGE_KEYS}
    return {"scores": {k: v for k, v in scores.items() if v is not None},
            "overall": number(row.get("judge_overall")),
            "rationale": row.get("judge_rationale", "")}


def free(model, pk: str):
    """The exported id, if nothing here uses it yet."""
    value = number(pk, int)
    return value if value and not model.objects.filter(pk=value).exists() else None


class Command(BaseCommand):
    help = "Load shared runs, outputs and ratings from exports/*.csv (safe to run again)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=str(settings.BASE_DIR / "exports"), help="Folder with the CSV files.")

    @transaction.atomic
    def handle(self, *args, **opts):
        folder = Path(opts["dir"])
        runs_csv, outputs_csv, ratings_csv = (folder / f"{name}.csv" for name in ("runs", "outputs", "ratings"))
        if not runs_csv.exists() or not outputs_csv.exists():
            raise CommandError(f"Need runs.csv and outputs.csv in {folder}")

        outputs_by_run: dict[str, list[dict]] = {}
        for row in read(outputs_csv):
            outputs_by_run.setdefault(row["run_id"], []).append(row)
        models = {m.openrouter_id: m for m in LLMModel.objects.all()}

        new_runs = kept_runs = new_outputs = scored = 0
        output_ids: dict[str, ModelOutput] = {}   # exported output_id -> output here
        for row in sorted(read(runs_csv), key=lambda r: number(r["run_id"], int) or 0):
            created = when(row.get("created_at", ""))
            task = row["task"] if row["task"] in Task.values else Task.FOLLOWUP
            run = ComparisonRun.objects.filter(created_at=created, task=task, message_text=row["message_text"]).first()
            if run:
                kept_runs += 1
                existing = list(run.outputs.all())
                for out_row in outputs_by_run.get(row["run_id"], []):
                    match = next((o for o in existing if o.model_name == out_row["model_name"]
                                  and o.text == out_row["output_text"]), None)
                    if match:
                        output_ids[out_row["output_id"]] = match
                continue

            run = ComparisonRun.objects.create(
                pk=free(ComparisonRun, row["run_id"]),
                source_message=PatientMessage.objects.filter(text=row["message_text"]).first(),
                message_text=row["message_text"],
                category=row.get("category") or "other",
                task=task,
                ehr_summary=row.get("ehr_summary", ""),
                reference_text=row.get("clinician_reference", ""),
                mock_mode=row.get("mock_mode") == "True",
                judge_model=row.get("judge_model", ""),
            )
            if created:
                ComparisonRun.objects.filter(pk=run.pk).update(created_at=created)
            new_runs += 1

            for out_row in outputs_by_run.get(row["run_id"], []):
                analysis = analysis_from(out_row)
                text, error = out_row.get("output_text", ""), out_row.get("error", "")
                if "gt_score" not in analysis and text.strip() and not error and run.reference_text.strip():
                    lexical = (ground_truth_for_questions if task == Task.FOLLOWUP else ground_truth_for_response)
                    analysis.update({**lexical(text, run.reference_text), "gt_method": "lexical"})
                    scored += 1
                output = ModelOutput.objects.create(
                    pk=free(ModelOutput, out_row.get("output_id")),
                    run=run, llm=models.get(out_row.get("openrouter_id", "")),
                    model_name=out_row["model_name"], openrouter_id=out_row.get("openrouter_id", ""),
                    text=text, error=error,
                    latency_ms=number(out_row.get("latency_ms"), int),
                    attempts=number(out_row.get("attempts"), int) or 1,
                    truncated=out_row.get("truncated") == "True",
                    prompt_tokens=number(out_row.get("prompt_tokens"), int),
                    completion_tokens=number(out_row.get("completion_tokens"), int),
                    analysis=analysis, judge=judge_from(out_row),
                )
                output_ids[out_row["output_id"]] = output
                new_outputs += 1

        ratings = 0
        if ratings_csv.exists():
            for row in read(ratings_csv):
                output = output_ids.get(row.get("output_id", ""))
                if not output or not row.get("rater"):
                    continue
                scores = {key: number(row.get(key), int) for key in DIMENSIONS}
                rating, made = HumanRating.objects.get_or_create(
                    output=output, rater=row["rater"],
                    defaults={"run": output.run, "blind_label": row.get("blind_label", ""),
                              "comment": row.get("comment", ""), **scores})
                if made:
                    if row.get("created_at"):
                        HumanRating.objects.filter(pk=rating.pk).update(created_at=when(row["created_at"]))
                    ratings += 1

        self.stdout.write(self.style.SUCCESS(
            f"Exports: {new_runs} new run(s), {kept_runs} already here; {new_outputs} output(s) added"
            f" ({scored} given a word-overlap score); {ratings} rating(s) added."))
