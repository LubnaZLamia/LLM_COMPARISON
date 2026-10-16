"""Does the automatic metric agree with the clinician? Measure it.

Every metric in this project is a claim about quality. The blind ratings in the
Rate portal are the only human judgement available, so a metric is good exactly
to the extent that it moves with them.

    python manage.py validate_metrics
    python manage.py validate_metrics --csv out.csv  # one row per rated output

Reported per metric:
  Spearman rho   rank correlation with the clinician's overall score, over all rated outputs
  95% CI         percentile interval from 1000 bootstrap resamples OF RUNS, not of
                 outputs, because three outputs from one run are not independent
  Kendall tau    within-run agreement: of the output pairs a rater ranked, how many
                 the metric ranks the same way (1.0 = always, 0 = coin toss, -1 = backwards)
  n              rated outputs the metric could be computed for

No SciPy: every statistic here is a few lines, and keeping the dependency list
short matters more than saving them.
"""
from __future__ import annotations

import csv
import random
from statistics import mean

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.comparisons.models import ComparisonRun
from apps.ratings.models import DIMENSIONS, HumanRating

BOOTSTRAP = 1000
SEED = 20261001   # fixed, so the same data gives the same intervals every time


# --- statistics -------------------------------------------------------------

def ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(ranks(xs), ranks(ys)) if len(xs) >= 3 else None


def kendall_within(groups: list[list[tuple[float, float]]]) -> tuple[float | None, int]:
    """Agreement on pairs inside the same run. groups: [[(metric, human), ...], ...]"""
    concordant = discordant = 0
    for rows in groups:
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                (m1, h1), (m2, h2) = rows[i], rows[j]
                if h1 == h2 or m1 == m2:
                    continue          # a tie on either side tells us nothing
                if (m1 - m2) * (h1 - h2) > 0:
                    concordant += 1
                else:
                    discordant += 1
    total = concordant + discordant
    return ((concordant - discordant) / total, total) if total else (None, 0)


def bootstrap_ci(by_run: dict[int, list[tuple[float, float]]], statistic) -> tuple[float, float] | None:
    """Percentile interval, resampling whole runs so outputs from one run stay together."""
    keys = list(by_run)
    if len(keys) < 4:
        return None
    rng = random.Random(SEED)
    values = []
    for _ in range(BOOTSTRAP):
        picked = [by_run[rng.choice(keys)] for _ in keys]
        rows = [row for group in picked for row in group]
        value = statistic(rows, picked)
        if value is not None:
            values.append(value)
    if len(values) < BOOTSTRAP // 2:
        return None
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def weighted_kappa(a: list[int], b: list[int], lo: int = 1, hi: int = 5) -> float | None:
    """Quadratic weighted Cohen's kappa — the usual agreement figure for ordered scales."""
    if len(a) < 2:
        return None
    size = hi - lo + 1
    observed = [[0.0] * size for _ in range(size)]
    for x, y in zip(a, b):
        observed[x - lo][y - lo] += 1
    n = len(a)
    rows = [sum(r) for r in observed]
    cols = [sum(observed[i][j] for i in range(size)) for j in range(size)]
    num = den = 0.0
    for i in range(size):
        for j in range(size):
            w = ((i - j) / (size - 1)) ** 2
            num += w * observed[i][j]
            den += w * rows[i] * cols[j] / n
    return 1 - num / den if den else None


# --- the metrics under test -------------------------------------------------

METRICS = [
    ("Clinician Match (meaning)", lambda a: a.get("gt_score")),
    ("Clinician Match (words)", lambda a: a.get("gt_score_lexical")),
    ("Judge score", None),               # read from output.judge, handled below
    ("Questions asked", lambda a: a.get("question_count")),
    ("Topics covered", lambda a: len(a["topics"]) if a.get("topics") is not None else None),
    ("Agreement with others", lambda a: a.get("agreement")),
    ("Reply length", lambda a: a.get("word_count")),
    ("Reading grade (lower is easier)", lambda a: a.get("grade_level")),
    ("Key elements", lambda a: len(a["elements"]) if a.get("elements") is not None else None),
]


class Command(BaseCommand):
    help = "Correlate every automatic metric with the blind clinician ratings."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default="", help="Write one row per rated output to this file.")

    def handle(self, *args, **opts):
        ratings = list(HumanRating.objects.select_related("output", "run"))
        if not ratings:
            self.stderr.write(self.style.ERROR(
                "No clinician ratings yet. Rate a few runs at /rate/ first — the whole point is to "
                "compare the metrics against human judgement."))
            return

        # Average the raters for each output, so one output is one data point.
        by_output: dict[int, list[HumanRating]] = {}
        for r in ratings:
            by_output.setdefault(r.output_id, []).append(r)

        rows = []
        for output_id, group in by_output.items():
            overalls = [r.overall for r in group if r.overall is not None]
            if not overalls:
                continue
            out = group[0].output
            if out.error or not out.analysis:
                continue
            rows.append({
                "run": out.run_id,
                "output": output_id,
                "model": out.model_name,
                "human": mean(overalls),
                "raters": len(group),
                "analysis": out.analysis,
                "judge": (out.judge or {}).get("overall"),
            })

        if len(rows) < 3:
            self.stderr.write(self.style.ERROR(f"Only {len(rows)} rated output(s); need at least 3."))
            return

        runs = len({r["run"] for r in rows})
        raters = len({r.rater.strip().lower() for r in ratings})
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{len(rows)} rated outputs across {runs} runs, from {raters} rater(s)\n"))

        self.report(rows)
        self.inter_rater(ratings)
        if opts["csv"]:
            self.write_csv(rows, opts["csv"])

    # --- reporting ---------------------------------------------------------

    def report(self, rows):
        humans = [r["human"] for r in rows]
        if len(set(humans)) == 1:
            self.stdout.write(self.style.WARNING(
                "Every rated output has the same human score, so no metric can correlate with anything. "
                "Rate a few outputs you actually disagree about.\n"))
            return

        self.stdout.write(f"{'Metric':34} {'Spearman':>9}  {'95% CI':>16}  {'Kendall (pairs)':>16}  {'n':>4}")
        self.stdout.write("-" * 86)
        results = {}
        for label, getter in METRICS:
            pairs = []
            for row in rows:
                value = row["judge"] if getter is None else getter(row["analysis"] or {})
                if value is not None:
                    pairs.append((row["run"], float(value), row["human"]))
            if len(pairs) < 3:
                continue
            xs = [p[1] for p in pairs]
            ys = [p[2] for p in pairs]
            rho = spearman(xs, ys)

            by_run: dict[int, list[tuple[float, float]]] = {}
            for run_id, metric, human in pairs:
                by_run.setdefault(run_id, []).append((metric, human))
            tau, compared = kendall_within(list(by_run.values()))
            ci = bootstrap_ci(by_run, lambda flat, groups: spearman([f[0] for f in flat], [f[1] for f in flat]))

            results[label] = (rho, ci, tau, len(pairs))
            self.stdout.write(
                f"{label:34} {self._n(rho):>9}  "
                f"{(f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else '—'):>16}  "
                f"{self._n(tau) + f' ({compared})':>16}  {len(pairs):>4}")

        self.stdout.write("")
        flat = [label for label, (rho, *_rest) in results.items() if rho is None]
        if flat:
            self.stdout.write(f"No correlation for: {', '.join(flat)} — the metric gave every rated output the "
                              f"same value, so there is nothing to correlate.")
        a = results.get("Clinician Match (meaning)")
        b = results.get("Clinician Match (words)")
        if a and b and a[0] is not None and b[0] is not None:
            diff = a[0] - b[0]
            verdict = ("meaning beats words" if diff > 0.05 else
                       "words beat meaning" if diff < -0.05 else "too close to call")
            self.stdout.write(self.style.SUCCESS(
                f"Semantic minus lexical: {diff:+.3f} Spearman — {verdict}."))
            self.stdout.write("Report the intervals, not just the difference: overlapping intervals mean the "
                              "data cannot yet separate the two methods.")
            self.stdout.write("A Kendall figure computed from a handful of pairs is noise — check the pair "
                              "count in brackets before quoting it.")
        else:
            self.stdout.write("Run `python manage.py recompute_metrics --method both` first so each rated "
                              "output carries both scores.")

    def inter_rater(self, ratings):
        """The ceiling: how much two raters agree with each other."""
        by_output: dict[int, dict[str, HumanRating]] = {}
        for r in ratings:
            by_output.setdefault(r.output_id, {})[r.rater.strip().lower()] = r
        overlapping = {k: v for k, v in by_output.items() if len(v) >= 2}
        if not overlapping:
            self.stdout.write("\nNo output has been rated by two people, so there is no agreement ceiling to "
                              "compare against. One second rater on ten outputs is enough.")
            return
        first, second = [], []
        for raters in overlapping.values():
            a, b = list(raters.values())[:2]
            for key in DIMENSIONS:
                x, y = getattr(a, key), getattr(b, key)
                if x is not None and y is not None:
                    first.append(x)
                    second.append(y)
        kappa = weighted_kappa(first, second)
        self.stdout.write(f"\nInter-rater agreement on {len(overlapping)} doubly-rated output(s): "
                          f"quadratic weighted kappa {self._n(kappa)} over {len(first)} dimension scores.")
        self.stdout.write("No metric should be expected to agree with a clinician more than this.")

    def write_csv(self, rows, path):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["run", "output", "model", "human_overall", "raters", "gt_meaning",
                             "gt_lexical", "judge", "question_count", "word_count", "grade_level"])
            for row in rows:
                a = row["analysis"] or {}
                writer.writerow([row["run"], row["output"], row["model"], f"{row['human']:.3f}", row["raters"],
                                 a.get("gt_score", ""), a.get("gt_score_lexical", ""), row["judge"] or "",
                                 a.get("question_count", ""), a.get("word_count", ""), a.get("grade_level", "")])
        self.stdout.write(self.style.SUCCESS(f"\nWrote {len(rows)} rows to {path} for your own analysis."))

    @staticmethod
    def _n(value) -> str:
        return "—" if value is None else f"{value:+.3f}"
