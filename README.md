# Beyond the Reply: Evaluating LLMs for Patient Portal Communication

An interactive Django web app for comparing how large language models handle patient portal messages.
Pick a task (follow-up questions or a reply to the patient), a patient message with its health record,
and several models. The app sends the same prompt to every model and shows their answers side by side.

Each answer is evaluated four ways:

- **Reference-free metrics**: questions asked, topics covered, agreement between models, length,
  reading grade and key safety elements.
- **Clinician Match**: how much of a real clinician's answer the model covered, by meaning and by word overlap.
- **LLM-as-a-judge** (optional): another model scores each answer from 1 to 5.
- **Human evaluation**: blind clinician rating and non-expert peer annotation.

Built on two public, clinician-annotated datasets: FB-Synth (FollowupBench) and SyPPM (see [Datasets](#datasets)).
All models go through [OpenRouter](https://openrouter.ai), so you only need one API key.

By Lubna Zahan Lamia, Jialin Yang and Sudarshan Regmi, Dartmouth College.

## Start the app (Windows)

1. Install **Python 3.10+** from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during setup.
2. Create an OpenRouter key at https://openrouter.ai/keys and add a few dollars of credit.
3. Double-click **`run.bat`**.

The first run takes a minute or two. It installs everything, asks you to paste your key, sets up the database
with example messages, picks the newest models from OpenRouter, and opens http://127.0.0.1:8000.
After that, double-clicking `run.bat` starts the app straight away. Close the window to stop it.

- Press Enter without a key to try the app in **mock mode** (fake outputs, no cost).
- To change the key later, edit `OPENROUTER_API_KEY` in `.env`.
- macOS/Linux: run `bash run.sh`.

## Pages

| URL | Page |
|---|---|
| `/` | **Compare**: pick a task, then a message from that task's dataset, then models, then generate |
| `/runs/<id>/` | **Results**: highlight tiles, side-by-side or table view, metrics, CSV download |
| `/chat/` | **Chat**: multi-turn chat with one model (you play the patient), streamed replies |
| `/annotate/` | **Peer annotation**: non-experts judge how similar each model output is to the clinician's answer |
| `/dataset/` | **Dataset manager**: add, edit, delete and search messages, and import/export CSV |
| `/rate/` | **Clinician rating**: blind human scoring of past runs, and the ratings CSV |
| `/help/` | How to use the app and how each metric works |
| `/admin/` | Manage models and browse past runs. Log in with `admin` / `admin` and change it with `python manage.py changepassword admin` |

## How models are compared

**Follow-up questions** (no ground truth needed): questions asked · topics covered (8 clinical areas) · agreement with other models ·
unique questions · repeated questions

**General response** (no ground truth needed): word count · reading grade level · key elements (emergency guidance, pointing the patient
to their care team, empathy) · topics addressed

**Against the clinician** (dataset and demo messages): **Clinician Match** — how much of the clinician's own answer
the model covered. Two implementations, and the app shows both at once:

| `MATCH_METHOD` | How a clinician item is matched |
|---|---|
| `meaning` | A small model reads the clinician's items and the model's answer and says which items are covered. A correct paraphrase counts; "any blood in your stool?" and "have you noticed rectal bleeding?" match |
| `lexical` | The original baseline: half of the item's content words must appear in one model sentence |
| `both` (default) | Both, side by side, so the meaning score always has a baseline beside it |

The alignment goes through the same chat endpoint, key and client as every other call — nothing new to configure. It
is one extra call per output at temperature 0, tried once: if it fails, times out or returns something unparseable,
the run falls back to the word-overlap score and marks it, so a hiccup never costs you the metric. Pick the model
with `MATCH_MODEL`; blank uses a cheap active one.

The alignment model never sees which system wrote an answer and is never asked how good it is — only which clinician
items are covered — so it is an annotator, not a second judge.

In both cases a broad model question can cover two clinician questions and counts for both: the score is coverage of
the clinician's items, and the model's own extra questions are not penalised.

```bat
.venv\Scripts\python manage.py check_matching                    REM 8 known cases against the real API: does it work?
.venv\Scripts\python manage.py recompute_metrics --method both   REM re-score past runs
.venv\Scripts\python manage.py validate_metrics                  REM compare every metric with the clinician ratings
```

`check_matching` is the one to run first. It sends eight cases with a known right answer — four paraphrases that
should count, four near-misses that should not, including "should the patient come in today?" against "there is no
need to come in today", which word overlap always gets wrong — and scores both methods on them. It exits non-zero if
the matcher gets more than one wrong, so a bad model or a dead key fails loudly instead of quietly degrading a metric.

`validate_metrics` is the one that matters for the write-up: each metric's Spearman correlation with the blind
clinician ratings, a bootstrap confidence interval resampled over runs, within-run Kendall agreement, and the
inter-rater ceiling. Neither command calls a model to regenerate anything.

**Judge model (optional)**: tick *Score with judge model* on the Compare page. Another LLM then rates each output from 1 to 5
(follow-ups: relevance, comprehensiveness, safety, clarity; responses: comprehensiveness, safety, empathy, clarity).
This costs one extra API call per model. Choose the judge by setting `JUDGE_MODEL` in `.env`, or tick "is judge" on a model in admin.

The automatic metrics use word overlap and keyword lists. They're fast, free and transparent, but they can miss paraphrases.

## Models

On first run, `seed_models` looks at OpenRouter's public model list and picks the newest general model from
OpenAI, Anthropic, Google and Meta. For Anthropic it picks the newest **Haiku** rather than the flagship, because the
big Claude models cost roughly ten times as much per token and this app makes one call per model per run.

Every time `run.bat` starts, the app checks the configured ids against OpenRouter and replaces any that were retired,
so a "No endpoints found for ..." error fixes itself on the next start. You can also run these by hand:

```bat
.venv\Scripts\python manage.py seed_models --refresh   REM re-pick the newest model per provider
.venv\Scripts\python manage.py check_models            REM list which configured ids still exist
```

You can also add, rename or disable models at `/admin/llms/llmmodel/`. Any OpenRouter model id works.

Each model gets a badge — one abstract shape per provider, tinted with that model's colour — so you can follow it
from the picker to the output cards, metric bars and tables. The shapes are plain geometry, deliberately not company
logos: `mark_for()` in `apps/llms/models.py` maps a provider name to a shape, the shapes live in
`templates/partials/icons.html` (`i-mark-*`), and the colour is the *Colour* field in admin.

## When a model fails

Calls are tried up to 3 times, with backoff, on temporary failures: HTTP 429 (the provider OpenRouter routed to was
rate limited or busy), 5xx, timeouts and empty replies. Permanent failures (bad key, no credit, retired model id) are
not retried. If a model still fails, its card shows the reason and the **Retry failed** button on the results page
re-runs just that model and merges the answer into the same run — useful mid-demo.

Tune in `.env`: `OPENROUTER_MAX_ATTEMPTS` (default 3), `OPENROUTER_RETRY_BACKOFF` (seconds, doubles each try),
`OPENROUTER_TIMEOUT`, and `LLM_MAX_TOKENS` (default 1500; too low cuts off models that think before answering).

## Chat

The **Chat** page is a normal multi-turn chat. You play the patient; the model answers as a health information assistant.

- Pick the model from the menu at the top. You can switch mid-chat, and each reply shows which model wrote it.
- Replies stream in word by word. **Stop** ends a reply early and keeps what was written. **Regenerate** replaces the
  last reply using the model currently picked.
- **Memory** is the chat history in the database. With each new message the model gets the system prompt plus the
  most recent `CHAT_HISTORY_MESSAGES` messages (default 20). Failed replies are left out. No external memory system is used.
- **Export** downloads a chat as JSON, with model, timing and token counts for each reply.
- Temporary failures are retried until the first words arrive. After that an error is shown with a **Try again** button.
- Mock mode works here too: replies are canned and no API call is made.

Edit the system prompt in `apps/chat/services/prompts.py`. Chats are local and shared by everyone using this copy of the app.

## Peer annotation (similarity to the clinician)

The **Annotate** page is a second human evaluation, separate from the clinician Rate page and open to people with
no medical training. It asks only what a non-expert can check: **how similar is a model's output to the clinician's
own answer?** It never asks about safety or clinical accuracy.

Each item shows the clinician's answer (**A**) beside one model output (**B**, model name hidden). All answers are
multiple choice:

1. **Per clinician item** (each question, or each sentence of the reply): *Covered / Partly / Not covered* in B.
   These are the same items the automatic Clinician Match uses, so the labels check that metric item by item.
2. **Overall, are A and B similar?** *Yes*, then roughly how much of A is covered (up to 25%, 25–50%, 50–75%,
   75–100%); or *No*, then why: different topics, misses most of A, too general, says the opposite, a different kind
   of text, or other (with a note).
3. **How much of B is not in A?** None, a little, some, most.
4. **How sure are you?** Sure, fairly sure, not sure. Plus an optional comment.

Items come from the comparison runs already stored: every successful output of a run that has clinician ground
truth. Mock-mode runs are left out.

Model outputs live in each person's own `db.sqlite3`, which git does not track, so shared runs travel as CSV files
in `exports/` (`runs.csv`, `outputs.csv`, `ratings.csv`). `run.bat` / `run.sh` load them on every start with
`python manage.py import_exports`: runs already in the database are skipped (matched on time, task and message),
exported ids are kept when free so `/runs/27/` is the same run everywhere, and outputs without a stored
clinician-match score get the word-overlap score. It calls no model, so it costs nothing.

The queue gives each output to `ANNOTATION_TARGET` people (default 2) before it opens new ones, so inter-annotator
agreement fills in early; *Skip* leaves an item for later.

**Results** (`/annotate/results/`) shows, per model, the share of clinician items people marked as covered next to
both automatic scores; how well each matcher (meaning and word overlap) agrees with people, per output (Spearman,
mean gap) and per item (agreement, Cohen's kappa); how often two people agree with each other (the ceiling for any
metric); and the reasons given for "not similar". Two CSVs: one row per annotation, and one row per clinician item
per annotation with the human label beside each matcher's call. Edit the questions and reasons in
`apps/annotations/models.py`.

## Clinician rating (human evaluation)

The **Rate** page collects human scores for runs you have already generated:

- The rater types a name or initials once (stored per rating, not a login).
- Outputs are shown **blind** as Model A / B / C, shuffled per rater, and revealed after saving.
- Each output is scored 1–5 on the standard human-evaluation dimensions: **fluency, coherence, relevance,
  factual correctness (hallucination), completeness, safety**, plus a free-text comment.
- Averages across raters show up as a **Clinician Score** card on the run's results page.
- **Download ratings CSV** gives one row per rater per output — ready for inter-rater agreement analysis.

Rating a run again updates that rater's scores instead of creating duplicates. Edit the rubric in
`apps/ratings/models.py` (`DIMENSIONS`).

## Datasets

Two public datasets of patient portal messages. Each pairs a semi-synthetic patient message with a real, de-identified
EHR summary and a clinician-written reference. Both are released for non-commercial research use (CC-BY-NC-4.0);
please cite the original papers.

| Dataset | Source | Task | Rows | Columns |
|---|---|---|---|---|
| FB-Synth (FollowupBench) | [PortalPal-AI/Followup-Q](https://huggingface.co/datasets/PortalPal-AI/Followup-Q) on Hugging Face; Gatto et al., ACL 2025 | Follow-up questions | 250 | EHR, Message, Questions |
| SyPPM | [PortalPal-AI collection](https://hf.co/collections/PortalPal-AI/evaluating-alignment-for-patient-message-response-drafting) on Hugging Face; Seegmiller et al., ACL 2026 | Patient reply | 100 | Patient Message, Patient EHR Summary, Clinician Response |

`data/` holds the processed copies the app imports automatically on startup:

| File | Made from | Gives ground truth for |
|---|---|---|
| `data/questions.csv` | FB-Synth, converted from Parquet to CSV, questions one per line | **Generate Follow-up Questions** (250 rows) |
| `data/responses.csv` | SyPPM, unchanged | **Generate General Response** (100 rows) |

Every SyPPM message also appears in FB-Synth, so rows are matched on the message text and merge into one dataset entry
rather than adding new patients. After merging, the two files contribute **227 messages — all 227 with reference
questions, 91 of those also with a reference reply**. `seed_dataset` adds 8 built-in demo messages on top (each with both
references, written by the team rather than clinicians), so a fresh install shows 235 and 99. The live counts are always
on the Compare and Help pages; nothing reads them from this file.

> **Known limitation.** Three message texts each appear 8–9 times in FB-Synth, each time with a different chart and
> different clinician questions. Because rows are matched on the message text alone, only the first copy of each is kept,
> which drops 23 FB-Synth rows and 9 SyPPM rows. Matching on the message *and* the EHR would keep them all.

Because of that, **each task has its own dataset** and the Compare page asks for the task first: step 2 then lists only
the messages with a reference for that task (235 for follow-up questions, 99 for replies, including the 8 demo messages),
so every run can be scored against a reference answer. Switching the task re-filters the list and moves the selection if the current message is
not in the new task's dataset; `CompareForm.clean()` rejects a mismatched pair server-side as well. Messages with no
ground truth at all never appear — write one on the *Write Your Own* tab instead, which runs every metric except the
clinician comparison.

Each message also carries an **EHR summary** — demographics, problem list, diagnoses, medications. It is sent to every
model (and to the judge) with the message, so they work from what the clinician saw. The results page shows it under
the patient message, next to the clinician's own answer.

Re-import or load your own files:

```bat
.venv\Scripts\python manage.py import_datasets --questions data\questions.csv --responses data\responses.csv --replace
.venv\Scripts\python manage.py import_datasets --questions my.parquet --limit 50     REM .parquet needs pandas + pyarrow
```

Column names are matched loosely, so `Message`, `Patient Message`, `EHR`, `Patient EHR Summary`, `Questions` and
`Clinician Response` are all recognised.

## Project structure

```
run.bat / run.sh         one-click setup + start
scripts/setup_env.py     creates .env and asks for the API key
config/                  Django settings and root urls
apps/
  core/                  help page, header banner, ensure_admin command
  dataset/               PatientMessage model, dataset manager, seed_dataset command
  llms/                  LLMModel model, OpenRouter client, seed_models / check_models
  ratings/               human rating: blind labels, rubric, Rate page, ratings CSV
  chat/                  multi-turn chat: Conversation/ChatMessage models, history + streaming (services/chat.py),
                         system prompt (services/prompts.py)
  annotations/           peer annotation: PeerAnnotation model, work queue (services/queue.py),
                         agreement with the automatic metrics (services/stats.py)
  comparisons/
    services/prompts.py  prompts for each task + judge criteria    <- edit prompts here
    services/runner.py   calls models in parallel, runs analysis + judge, saves results
    services/analysis.py output-based metrics                       <- edit metrics here
    services/judge.py    LLM-as-judge scoring
    services/summary.py  builds highlight tiles, panels and table details
    services/metrics.py  word-overlap matching against the clinician (the lexical baseline)
    services/matcher.py  meaning-based clinician matching (a small LLM aligns the two texts)
    management/commands/ check_matching, recompute_metrics, validate_metrics
data/                    the two datasets, imported on startup
exports/                 shared runs, outputs and ratings (CSV), imported on startup
templates/, static/      pages, styles, small JS
```

## Tests

```bat
.venv\Scripts\python manage.py test
```

> Research tool only. Model outputs are not medical advice.
