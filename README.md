# MUSE

An AI-assisted programming tutor that enforces three phases instead of allowing
free-form code generation:

1. **Specification** — the editor stays locked until the student has written a
   specification that satisfies the assignment's rubric.
2. **Guided implementation** — the student writes every character themselves.
   Reference questions get an answer; questions about their own code get a
   counter-question.
3. **Reflection** — at submission, the code is run against the specification the
   student wrote, and every discrepancy has to be explained in their own words
   before the submission is accepted.

The hard part is not code generation. It is classification and dialogue
management: deciding what kind of help is being asked for, and answering in a
way that preserves the student's reasoning rather than shortcutting it.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # add keys, or leave them blank to use the stub
python -m app.seed            # a course, 8 students, 2 assignments
uvicorn app.main:app --reload
```

Then open <http://localhost:8000> for the student flow and
<http://localhost:8000/instructor> for the dashboard.

With no API keys, MUSE runs on a deterministic offline stub so the whole flow is
clickable and testable without spending quota. `/api/health` tells you which
mode you are in, and both pages show a warning banner when the stub is live.

**After editing `.env`, stop and restart uvicorn.** `--reload` watches `.py`
files only, so a running server keeps whatever settings it read at startup — add
your keys to a server that started without them and it will happily go on
serving canned stub text.

To watch the whole thing run in your terminal:

```bash
python scripts/demo_flow.py
```

---

## Providers

Both Mistral and Groq expose OpenAI-compatible chat completions, so one client
shape covers them. Every call in the system goes through
`generate_structured()` in `app/llm/client.py` — **no module imports Mistral or
Groq directly.**

| | Provider | Model | Why |
|---|---|---|---|
| Primary | Mistral (`console.mistral.ai`, free "Experiment" tier, no card) | `mistral-small-latest` | ~1B tokens/month, but a single-digit requests-per-minute cap |
| Fallback | Groq (`console.groq.com`, no card) | `openai/gpt-oss-20b` | ~30 req/min, ~14,400/day **at the org level** — extra keys don't raise it |

Groq deprecated `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` in August
2026; don't build against them.

Groq is not only an outage hedge. Mistral's RPM cap is low enough that a class
of twenty students typing at the same time will hit it during normal use, which
is the case the fallback is really there for.

### Failover policy

Deliberately narrow, in `generate_structured()`:

- **429 / 5xx / timeout / connection error** → two quick retries on Mistral with
  a short backoff, then Groq. Not thirty seconds of politeness toward Mistral
  while a student watches a spinner.
- **Malformed JSON from the model** → raises `MalformedJSONError`, logged and
  surfaced as a 502. It does *not* fail over: a model that stopped honouring the
  JSON contract is a prompt regression, and failing over would hide it.
- **4xx other than rate limits** → raises immediately. That's a bug in our
  request; retrying it elsewhere just hides it.
- **Both providers down** → `ProviderExhaustedError`, surfaced as a 503 with a
  message a student can act on.

### Two tiers

Classification (question type, spec completeness, drift/revision/bug) and
dialogue generation (Socratic questions, clarifying questions, narrative
summaries) have different quality requirements, so they are separate settings:

```bash
DIALOGUE_MISTRAL_MODEL=mistral-large-latest   # upgrade the tier that matters
```

The dialogue tier is the one students experience as "good teaching" or
"annoying gatekeeping", and it is the lowest-volume tier — it is the first thing
to spend money on. Because every call routes through one function, adding a
third provider branch for that tier changes nothing in `app/modules/`.

---

## Architecture

```
app/
  llm/
    client.py       generate_structured() — the only place a provider is called
    prompts.py      the prompt shapes, one per task
    fake.py         deterministic offline stub for tests and no-key demos
  modules/
    spec_gate.py    Step 1 — extract_fields / next_gap / clarifying question
    tutor.py        Step 2 — classify, answer or counter-question, escape hatch
    sandbox.py      subprocess execution with a timeout and resource limits
    reflection.py   Step 3 — behavioral diff, then drift/revision/bug
    dashboard.py    Step 4 — SQL aggregation + on-demand narrative summary
    profiler.py     optional — dependency signals, with a visibility flag
  api/              FastAPI routers, one per module
  models.py         SQLAlchemy models (JSONB on Postgres, JSON on SQLite)
frontend/           Monaco editor + the three-phase student flow, no build step
schema.sql          the Postgres DDL, if you'd rather manage migrations by hand
```

### Data model

Six entities from the spec — `students`, `assignments`, `specifications`,
`code_snapshots`, `interactions`, `diff_results` — plus two operational tables:

- **`llm_calls`** — provider, model, latency, success, and whether the call fell
  back, for every single call. Read it before a demo.
- **`course_settings`** — dependency-profile visibility. Section 3.5 asks for
  this to be a data-model flag rather than a UI toggle, so the API refuses an
  instructor read when the course hasn't opted in.

Two shapes carry the structure:

```jsonc
// specifications.content
{ "goal": "...", "inputs": [...], "outputs": [...],
  "edge_cases": [...], "examples": [...], "explicit_exclusions": [...] }

// assignments.rubric
{ "entry_point": "fib",                       // what the diff engine calls
  "items": [{"id": "empty_input", "priority": 4,
             "text": "must state behavior on empty input"}] }
```

`entry_point` lives inside the rubric JSONB so the table columns stay exactly as
specified. It's what the Reflection Engine calls when it runs the student's code.

Postgres is the target. SQLite is the default only so the project boots with
zero infrastructure:

```bash
DATABASE_URL=postgresql+psycopg://muse:muse@localhost:5432/muse
```

---

## How each module behaves

**Specification Gate.** Extract → check the rubric → ask about exactly one unmet
item. Completeness is a checklist derived from the rubric, not one open-ended
"is this clear?" prompt — that is what makes the unlock checkable rather than a
vibe judgement. Every clarification round writes a *new version* row, so drift
can later be measured against version 1 and not just the locked one. After two
asks on the same gap the student can confirm their answer stands and move on;
without that, a genuinely ambiguous edge case turns the gate into a loop.

**Tutor Layer.** One classification call per message, with the current code diff
as context. `factual` gets a direct answer, `problem_solving` gets exactly one
counter-question. Students will learn to rephrase problem-solving questions as
pseudo-factual ones, so the Section 3.2 mitigation is enforced in code, not just
in the prompt: a message naming the student's own identifiers, or pasting a
traceback, is reclassified as `problem_solving` regardless of what the model
said (`tutor.deterministic_override`). After three unresolved counter-question
rounds on the same thread, a direct hint becomes available at a logged assist
cost — pure row-counting, no extra AI call.

**Reflection Engine.** Runs the declared examples in a subprocess sandbox
(wall-clock timeout always; CPU, address-space and file-size limits on POSIX).
Declared edge cases are turned into runnable inputs by one classification call
per submission — without that, the engine would only ever check the happy path
and would miss exactly the "never handled empty input" case that makes the whole
feature worth having. Each failing case gets one drift/revision/bug call with
the interaction log as evidence. Nothing is fixed; discrepancies are surfaced
and an explanation is required. The friction is the feature.

**Instructor Dashboard.** No AI calls at read time. Class patterns are a
group-by. The per-student narrative summary is one dialogue-tier call behind a
button.

---

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/assignments/{id}/specification` | POST | Start or continue the clarification dialogue |
| `/api/assignments/{id}/specification` | GET | Current specification + lock status + rubric |
| `/api/assignments/{id}/specification/lock` | POST | Force-lock after the 2nd ask on a gap |
| `/api/specifications/{id}/interactions` | POST | Student message + code diff → classification + response |
| `/api/specifications/{id}/interactions` | GET | Interaction history |
| `/api/specifications/{id}/interactions/escape-hatch` | POST | Direct hint, logs assist cost |
| `/api/specifications/{id}/snapshots` | POST | Editor autosave |
| `/api/specifications/{id}/submit` | POST | Behavioral diff + classification |
| `/api/diff-results/{id}/explanation` | POST | One student explanation |
| `/api/specifications/{id}/submission-status` | GET | Whether submission can finalize |
| `/api/specifications/{id}/finalize` | POST | Accept the submission |
| `/api/courses/{id}/students/{sid}/overview` | GET | Per-student history, transcript, discrepancies |
| `/api/courses/{id}/analytics/class-patterns` | GET | Aggregated class view |
| `/api/students/{id}/narrative-summary` | GET | On-demand summary (dialogue tier) |
| `/api/students/{id}/dependency-profile` | GET | `?viewer=student\|instructor` |
| `/api/ops/llm-calls` | GET | Provider/fallback/latency log |

Interactive docs at `/docs`.

---

## Before the demo

```bash
pytest -q                       # 52 tests, offline
python scripts/loadtest.py --students 20
```

The load test fires a burst of classification calls the way a class does and
reports which provider actually served each one. If Groq absorbs part of the
burst, that is the fallback doing its job. If you see `ProviderExhaustedError`,
both ceilings were hit.

For the live demo, put a paid key on the classification tier — even a few
dollars of Mistral's or Groq's paid tier removes the RPM ceiling — so the
audience never sees a 429:

```bash
MUSE_PRIMARY=groq   # or a paid Mistral key; flip back to free afterwards
```

Then check `/api/ops/llm-calls` or the provider-log panel on the dashboard.

---

## Tuning knobs

These are starting heuristics, not validated thresholds. Section 8 of the spec
flags each of them as something to tune once there are real transcripts.

| Setting | Default | What to watch |
|---|---|---|
| `GATE_MAX_ASKS_PER_GAP` | 2 | Force-lock rate. High = the gate is too strict for that assignment |
| `TUTOR_ESCAPE_HATCH_ROUNDS` | 3 | Escape-hatch usage rate. High = the Socratic default is miscalibrated |
| classifier override rate | — | `classifier_overridden` in the tutor response: how often students rephrase to game the classifier |
| `SANDBOX_TIMEOUT_SECONDS` | 5 | Per-case wall clock |

Two things worth knowing up front:

- The behavioral diff is only as good as the specification's declared examples
  and edge cases. An underspecified rubric produces weak reflection output no
  matter which model you point at it.
- Resource limits in the sandbox are POSIX-only. On Windows you get the
  wall-clock timeout and a scratch working directory; run the grader on Linux or
  in a container for the real thing.

---

## Testing

```bash
pytest -q                       # everything, on the offline stub
pytest tests/test_llm_client.py # failover policy specifically
```

The failover tests fake provider errors directly rather than going through the
API, because that policy is the load-bearing part of this system under classroom
load: retry-then-fall-over on rate limits, raise on malformed JSON, raise on
4xx, and log the provider for every call.
