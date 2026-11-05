"""What the annotator sees about the patient: the message, and the chart summary the models were given.

The chart summaries in the datasets are plain text with ###Section### headers,
"Label: a - b - c" lines and "-ITEM" lines. chart_sections() turns that into
sections of labelled lists so it can be read at a glance; anything it can't
place still shows, as an unlabelled list in its section.
"""
from __future__ import annotations

import re

_HEADER = re.compile(r"^#{2,}\s*(.+?)\s*#{2,}:?\s*$")
_LABELLED = re.compile(r"^([A-Z][^:]{0,60}):\s*(.*)$")
# " - " separates list entries, but not inside a range such as "Between 70 - 75".
_ENTRY_SEP = re.compile(r"(?<!\d)\s+-\s+(?!\d)")
_MAX_IN_TITLE = re.compile(r"\s*\(max \d+\)", re.IGNORECASE)
# The datasets' own labels, shortened for the page. Anything else shows as written.
SHORT_LABELS = {
    "diagnoses (past year)": "Past year",
    "diagnoses (older)": "Older",
    "active (start date before message, not yet ended)": "Taking now",
    "recently ended within 30-days of message": "Stopped in the last 30 days",
}


def _entries(value: str) -> list[str]:
    return [part.strip(" -•*") for part in _ENTRY_SEP.split(value) if part.strip(" -•*")]


def chart_sections(text: str) -> list[dict]:
    """[{"title": "Demographics", "groups": [{"label": "Age", "items": ["Between 70 - 75"]}, ...]}, ...]"""
    sections: list[dict] = []
    section = group = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _HEADER.match(line)
        if header:
            section, group = {"title": _MAX_IN_TITLE.sub("", header.group(1)), "groups": []}, None
            sections.append(section)
            continue
        if section is None:
            section = {"title": "", "groups": []}
            sections.append(section)
        labelled = _LABELLED.match(line)
        if labelled and not line.startswith("-") and len(_entries(labelled.group(1))) == 1:
            label, value = labelled.group(1).strip(), labelled.group(2).strip()
            label = SHORT_LABELS.get(label.lower(), label)
            section["groups"].append({"label": label, "items": _entries(value)})
            # "Age: 70 - 75" is complete; "Active (...):" collects the "-ITEM" lines below it.
            group = None if value else section["groups"][-1]
            continue
        if group is None:
            group = {"label": "", "items": []}
            section["groups"].append(group)
        group["items"] += _entries(line)
    for section in sections:
        section["groups"] = [g for g in section["groups"] if g["items"]]
    return [s for s in sections if s["groups"]]


def for_run(run) -> dict:
    """The patient's message and chart for one comparison run.

    The chart comes from the run, which is what the models were sent. Runs made
    before chart summaries existed fall back to the dataset message's chart, and
    say so, since the models never saw it.
    """
    chart, sent = run.ehr_summary.strip(), True
    if not chart and run.source_message_id:
        chart, sent = run.source_message.ehr_summary.strip(), False
    return {
        "message": run.message_text.strip(),
        "chart": chart,
        "chart_sections": chart_sections(chart) if chart else [],
        "chart_sent": sent and bool(chart),
    }
