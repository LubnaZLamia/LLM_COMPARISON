# Demo run-sheet

Checked end to end on 30 Sep 2026 against your own database and your own settings: every page loads,
no JavaScript errors, no server errors, 44/44 tests pass, and a clean install from `run.bat` works from scratch.

## Before you start (2 minutes)

1. Double-click **`run.bat`** and wait for the browser to open at `http://127.0.0.1:8000`.
2. Check the header: **no orange banner** means the API key is found and real models will be called.
3. Do one throwaway run before the meeting so the first real API call has already happened.

## The 6-minute walkthrough

| # | Do | Say |
|---|---|---|
| 1 | Land on the Compare page | "Four steps: task, patient message, models, results." |
| 2 | Leave **Generate Follow-up Questions** selected. Point at the two counts on the task cards | "Each task has its own clinician ground truth — 235 messages have clinician questions, only 99 have a written reply, so the app only offers what it can actually score." |
| 3 | Pick the top message (a real portal message with a chart summary) | "Real patient-portal message, and the models also get the chart summary the clinician had." |
| 4 | Tick 3 models + **Score with judge model**, press Generate | "Same prompt to every model, in parallel." |
| — | (Models offered: GPT-4o, Claude Haiku 4.5, Llama 3.1 70B, Gemini 3.8 Flash) | |
| 5 | Results: read the highlight strip left to right | "Closest to the clinician, asked the most, covered the most ground, most in agreement, top judge score." |
| 6 | Scroll to the metric cards | "Clinician Match is the ground-truth one. The rest need no ground truth at all — that's the point: it still works on a message no clinician has answered." |
| 7 | Click **Table view** | "Per model: topics covered, questions only it asked, what it missed from the clinician, and the judge's reasoning." |
| 8 | Click **Rate** | "Blind human evaluation — Model A/B/C, shuffled per rater, six NLP dimensions. Scores come back as a Clinician Score card on the run." |
| 9 | Back on the results page, point at Clinician Score | "Automatic metrics, an LLM judge and human raters, side by side on the same run." |

Switch the task to **Generate General Response** if asked about the second task — the message list drops from
235 to 99 in front of them, which makes the "each task has its own dataset" point by itself.

## If something goes wrong

| What you see | What to do |
|---|---|
| One model card says **Request failed** | Press **Retry failed** above the cards. It re-runs only that model and merges the answer into the same run. Rate limits are already retried 3× automatically. |
| Everything fails with **401** | The API key in `.env` is wrong or out of credit. Say so and open a saved run instead: find its number on the **Rate** page and go to `http://127.0.0.1:8000/runs/<number>/` — the old runs are all still there. |
| It hangs for more than a minute | Reload the page; the run may already have been saved. Check `/rate/` for the newest run number. |
| No internet at the venue | Set `LLM_MOCK_MODE=true` in `.env` and restart — the whole app works with canned answers, and past runs still render. |

**Safest fallback:** run #23, #27 or #30 are already stored with full metrics, judge scores and ratings.
Open `http://127.0.0.1:8000/runs/27/` directly and the meeting can go on with no API calls at all.

## The meaning-based match (new)

Nothing extra to run: `run.bat` is the only thing you start, exactly as before.

Once, before the meeting, run `python manage.py check_matching`. It takes about ten seconds, calls the real API with
eight cases that have a known right answer, and prints how the meaning-based match and the word-overlap baseline each
scored on them. If it says "Matching works", the feature is proven on your machine with your key.

On the results page, **Clinician Match** (meaning) sits next to **Clinician Match (words)**. The gap between those
two bars is the finding — the share of the clinician's questions the old word-overlap metric was throwing away
because the model used different words. Table view lists which clinician items each model covered and which it
missed, which is the error analysis, live.

If the alignment call fails for any reason, the card silently falls back to the word-overlap score, so there is no
failure mode here that can interrupt the demo.

## Answers to the questions you'll get

- **"How is it scored against the clinician?"** Per item. Each clinician question (or each sentence of the clinician's
  reply) counts as matched when at least half of its meaningful words appear in one single model question/sentence.
  Score = matched ÷ total. Extra material the model adds is not penalised.
- **"Why 0.5?"** A judgement call, not fitted to data — set with `GT_MATCH_RECALL` in `.env`, and the ranking is
  what matters rather than the absolute number.
- **"Why is the match so low?"** It matches words, not meaning. A model can ask a clinically excellent question in
  different words and score zero. That's exactly why the judge model and the blind clinician rating are there.
- **"Can it use other models?"** Any OpenRouter model id; add or swap them in `/admin/llms/llmmodel/`. Retired ids
  are repaired automatically on every start.
- **"What does a run cost?"** Four models plus the judge, on a short message: fractions of a cent. Claude runs on
  **Haiku 4.5** ($1 in / $5 out per 1M tokens) rather than the flagship, which is roughly ten times dearer.
