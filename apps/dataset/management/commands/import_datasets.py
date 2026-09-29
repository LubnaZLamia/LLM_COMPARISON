"""Import the ground-truth datasets.

Two files, two tasks:

* a **questions** dataset (parquet or csv) with columns Message / EHR / Questions,
  where Questions is the clinician's list of follow-up questions;
* a **responses** dataset (csv or parquet) with columns
  Patient Message / Patient EHR Summary / Clinician Response.

The same patient message can appear in both files. Rows are matched on the
message text, so such a message ends up as one dataset entry carrying both
reference outputs.

    python manage.py import_datasets --questions data/questions.parquet --responses data/responses.csv
    python manage.py import_datasets --questions q.parquet --responses r.csv --limit 50 --replace

Parquet needs pyarrow (`pip install pyarrow`); csv needs nothing.
"""
from __future__ import annotations

import ast
import csv as csvlib
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.dataset.models import Category, PatientMessage

# Column name -> our field. Lower-cased, non-letters stripped, so
# "Patient EHR Summary" and "ehr_summary" both match.
ALIASES = {
    "message": "text", "patientmessage": "text", "question": "text",
    "ehr": "ehr", "ehrsummary": "ehr", "patientehrsummary": "ehr", "context": "ehr",
    "questions": "questions", "followupquestions": "questions", "referencefollowup": "questions",
    "clinicianresponse": "response", "response": "response", "referenceresponse": "response",
    "answer": "response", "clinicianreply": "response",
}

CATEGORY_HINTS = [
    # Order matters: the first match wins, so specific topics come before admin ones.
    # No trailing \b, so "pains", "swelling" and "medications" all match.
    (Category.MEDICATION, r"\b(medication|medicine|prescri|dosage|\bdose|pill|refill|antibiotic|statin|insulin|inhaler|side effect)"),
    (Category.TEST_RESULTS, r"\b(result|lab work|labs\b|blood test|bloodwork|blood work|scan|mri|x-?ray|biopsy|cholesterol|a1c|ultrasound)"),
    (Category.TREATMENT_RESPONSE, r"\b(physical therapy|since starting|not working|isn't working|still (hurt|having|feel)|after (my )?surgery|treatment)"),
    (Category.SYMPTOMS, r"\b(pain|ache|fever|cough|rash|swell|dizz|nausea|vomit|symptom|bleed|tired|fatigue|itch|sore|burning|cramp|sneez|congest|runny nose|diarrh|short of breath|sick|unwell)"),
    (Category.SCHEDULING, r"\b(appointment|schedul|referral|paperwork|insurance|form to|forms\b)"),
]


def guess_category(text: str) -> str:
    low = text.lower()
    for category, pattern in CATEGORY_HINTS:
        if re.search(pattern, low):
            return category
    return Category.OTHER


def normalise(name: str) -> str:
    return re.sub(r"[^a-z]", "", str(name).lower())


def read_rows(path: Path) -> list[dict]:
    """Read a .parquet or .csv file into a list of {field: value} using ALIASES."""
    if path.suffix.lower() == ".parquet":
        try:
            import pandas as pd
        except ImportError:
            raise CommandError("Reading .parquet needs pandas + pyarrow: pip install pandas pyarrow")
        try:
            frame = pd.read_parquet(path)
        except ImportError:
            raise CommandError("Reading .parquet needs pyarrow: pip install pyarrow")
        raw = frame.to_dict("records")
    elif path.suffix.lower() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(encoding="utf-8-sig", newline="") as fh:
            raw = list(csvlib.DictReader(fh, delimiter=delimiter))
    else:
        raise CommandError(f"Unsupported file type: {path.suffix} (use .parquet or .csv)")

    rows = []
    for item in raw:
        row = {}
        for key, value in item.items():
            field = ALIASES.get(normalise(key))
            if field:
                row[field] = value
        if row.get("text"):
            rows.append(row)
    return rows


def as_question_list(value) -> list[str]:
    """Questions arrive as a numpy array, a python list, or a string that looks like one."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") :
            try:  # "['a' 'b']" (numpy repr) has no commas, so add them before parsing
                return [str(q).strip() for q in ast.literal_eval(re.sub(r"'\s+'", "', '", text)) if str(q).strip()]
            except (ValueError, SyntaxError):
                return [q.strip(" '\"") for q in re.findall(r"'([^']+)'|\"([^\"]+)\"", text) for q in q if q]
        return [line.strip() for line in text.splitlines() if line.strip()]
    try:  # numpy array / list / tuple
        return [str(q).strip() for q in list(value) if str(q).strip()]
    except TypeError:
        return []


def clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


class Command(BaseCommand):
    help = "Import ground-truth datasets for the follow-up questions and general response tasks."

    def add_arguments(self, parser):
        parser.add_argument("--questions", help="File with clinician follow-up questions (.parquet or .csv)")
        parser.add_argument("--responses", help="File with clinician responses (.csv or .parquet)")
        parser.add_argument("--limit", type=int, default=0, help="Import at most this many rows per file.")
        parser.add_argument("--replace", action="store_true",
                            help="Delete previously imported rows from the same file first.")

    def handle(self, *args, **opts):
        if not opts["questions"] and not opts["responses"]:
            raise CommandError("Give --questions and/or --responses with a file path.")

        totals = {"created": 0, "updated": 0}
        for kind, option in (("questions", "questions"), ("response", "responses")):
            path_str = opts[option]
            if not path_str:
                continue
            path = Path(path_str).expanduser()
            if not path.exists():
                raise CommandError(f"File not found: {path}")

            rows = read_rows(path)
            if opts["limit"]:
                rows = rows[: opts["limit"]]
            if not rows:
                raise CommandError(f"No usable rows in {path.name}. Expected a message column.")

            already = PatientMessage.objects.filter(source=path.name[:60]).count()
            if already and not opts["replace"]:
                self.stdout.write(f"{path.name}: already imported ({already} messages). Use --replace to re-import.")
                continue

            if opts["replace"]:
                deleted, _ = PatientMessage.objects.filter(source=path.name[:60]).delete()
                if deleted:
                    self.stdout.write(f"Removed {deleted} row(s) previously imported from {path.name}.")

            created = updated = 0
            for row in rows:
                text = clean(row.get("text"))
                if not text:
                    continue
                fields = {
                    "ehr_summary": clean(row.get("ehr")),
                    "source": path.name[:60],
                    "is_featured": True,
                }
                if kind == "questions":
                    questions = as_question_list(row.get("questions"))
                    if not questions:
                        continue
                    fields["reference_followup"] = "\n".join(questions)
                else:
                    response = clean(row.get("response"))
                    if not response:
                        continue
                    fields["reference_response"] = response

                existing = PatientMessage.objects.filter(text=text).first()
                if existing:  # same message in both files: keep both references
                    for field, value in fields.items():
                        if value and not getattr(existing, field):
                            setattr(existing, field, value)
                    existing.save()
                    updated += 1
                else:
                    PatientMessage.objects.create(text=text, category=guess_category(text), **fields)
                    created += 1

            self.stdout.write(self.style.SUCCESS(
                f"{path.name}: {created} new message(s), {updated} merged into existing ones."))
            totals["created"] += created
            totals["updated"] += updated

        total = PatientMessage.objects.count()
        with_q = PatientMessage.objects.exclude(reference_followup="").count()
        with_r = PatientMessage.objects.exclude(reference_response="").count()
        self.stdout.write(self.style.SUCCESS(
            f"Dataset now holds {total} messages: {with_q} with reference questions, {with_r} with a reference response."))
