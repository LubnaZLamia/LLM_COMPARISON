"""Peer annotations as files, so they travel through git with the rest of exports/.

Annotations live in each person's own db.sqlite3, which git does not track. To share
them, each annotator's work is written to exports/annotations/<name>.csv, and
import_exports loads every file in that folder. One file per person means two people
never change the same file, so git has nothing to conflict on.

Output ids can differ between machines, so a row finds its output by a fingerprint of
the run's time and task and the model's name and text. A row whose output isn't here
is counted and skipped, and so is a damaged one: a bad file must not stop the app
from starting. Loading is safe to repeat: the newer of two versions of an annotation
wins, and nothing is ever deleted.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import timezone
from pathlib import Path

from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from apps.comparisons.models import ModelOutput

from ..models import PeerAnnotation

COLUMNS = ["output_key", "output_id", "run_id", "task", "model_name", "annotator", "reference_items", "item_labels",
           "item_score", "candidate_units", "item_links", "unit_score", "similar", "coverage", "reasons",
           "other_reason", "extra", "confidence", "comment", "seconds", "created_at", "updated_at"]
JSON_FIELDS = ("reference_items", "item_labels", "candidate_units", "item_links", "reasons")
TEXT_FIELDS = ("other_reason", "extra", "confidence", "comment")


def output_key(output: ModelOutput) -> str:
    """The same output has the same key on every machine, whatever its id there."""
    created = output.run.created_at.astimezone(timezone.utc).isoformat()
    parts = (created, output.run.task, output.model_name, output.text)
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def file_name(annotator: str) -> str:
    name = slugify(annotator)
    return f"{name or hashlib.sha256(annotator.lower().encode()).hexdigest()[:12]}.csv"


def _blank(value):
    return "" if value is None else value


def _row(a: PeerAnnotation) -> dict:
    row = {name: json.dumps(getattr(a, name), ensure_ascii=False) for name in JSON_FIELDS}
    row.update({name: getattr(a, name) for name in TEXT_FIELDS})
    row.update({
        "output_key": output_key(a.output), "output_id": a.output_id, "run_id": a.output.run_id,
        "task": a.output.run.task, "model_name": a.output.model_name, "annotator": a.annotator,
        "item_score": _blank(a.item_score), "unit_score": _blank(a.unit_score),
        "similar": {True: "yes", False: "no"}.get(a.similar, ""), "coverage": _blank(a.coverage),
        "seconds": _blank(a.seconds), "created_at": a.created_at.isoformat(), "updated_at": a.updated_at.isoformat(),
    })
    return row


def write(folder: Path, annotator: str = "") -> dict[Path, int]:
    """Write one CSV per annotator, or just the named one. Returns {file: rows}."""
    annotations = PeerAnnotation.objects.select_related("output__run")
    if annotator:
        annotations = annotations.filter(annotator__iexact=annotator)
    by_file: dict[Path, list[dict]] = {}
    for a in annotations:
        by_file.setdefault(folder / file_name(a.annotator), []).append(_row(a))
    folder.mkdir(parents=True, exist_ok=True)
    for path, rows in by_file.items():
        # Sorted so that exporting unchanged work gives an unchanged file, and a clean git diff.
        rows.sort(key=lambda r: (r["output_key"], r["annotator"]))
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    return {path: len(rows) for path, rows in by_file.items()}


def _number(value: str, cast):
    return None if value in ("", None) else cast(value)


def _fields(row: dict) -> dict:
    """The model fields from one CSV row. Raises KeyError or ValueError if the row is damaged."""
    fields = {name: json.loads(row[name]) for name in JSON_FIELDS}
    fields.update({name: row[name] for name in TEXT_FIELDS})
    if len(fields["item_labels"]) != len(fields["reference_items"]):
        raise ValueError("one label per clinician item")
    fields.update({
        "item_score": _number(row["item_score"], float), "unit_score": _number(row["unit_score"], float),
        "similar": {"yes": True, "no": False}.get(row["similar"]), "coverage": _number(row["coverage"], int),
        "seconds": _number(row["seconds"], int),
    })
    return fields


def load(folder: Path) -> Counter:
    """Load every CSV in the folder. Returns counts of new, updated, kept, unmatched and bad rows."""
    counts: Counter = Counter()
    paths = sorted(folder.glob("*.csv")) if folder.is_dir() else []
    if not paths:
        return counts
    outputs = {output_key(o): o for o in ModelOutput.objects.select_related("run")}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            output = outputs.get(row.get("output_key", ""))
            if output is None:
                counts["unmatched"] += 1
                continue
            try:
                fields = _fields(row)
                annotator = row["annotator"].strip()
                created, updated = parse_datetime(row["created_at"]), parse_datetime(row["updated_at"])
                if not annotator or created is None or updated is None:
                    raise ValueError("annotator and times are required")
            except (KeyError, TypeError, ValueError):
                counts["bad"] += 1
                continue
            existing = PeerAnnotation.objects.filter(output=output, annotator__iexact=annotator).first()
            if existing and existing.updated_at >= updated:
                counts["kept"] += 1
                continue
            annotation = existing or PeerAnnotation(output=output, annotator=annotator)
            for name, value in fields.items():
                setattr(annotation, name, value)
            annotation.save()
            # save() stamps the current time; keep the annotator's own, so "newer wins" stays true.
            PeerAnnotation.objects.filter(pk=annotation.pk).update(created_at=created, updated_at=updated)
            counts["updated" if existing else "new"] += 1
    return counts
