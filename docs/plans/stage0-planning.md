# Stage 0: Planning (topic → research questions)

## Why this stage exists

Before reading any source, the agent decides **what a good brief on this topic should answer**. That plan does four jobs later on:

| Used by | For |
|---|---|
| Stage 2 (cleaning) | **Subject terms** decide whether a paragraph is about the topic at all. **Question terms** show whether it answers a specific question. Today "AI / job / losses" misses paragraphs that say "unemployment" or "automation". |
| Stage 3 (claims) | Each claim is tagged with a **question ID** and, for numbers, a **standard metric ID** (e.g. `job_loss_rate` vs `ai_exposure_share`). |
| Stage 4 (align) | Claims are grouped by question, then by scope. Standard metric IDs and facets give shared names to group on. |
| Stage 5 (judge) | **Gaps** = questions (or facets) that no source answers. Metrics marked "easily confused" stop false contradictions. |

**Input:** the topic. **Output:** `runs/<id>/plan.json`.
Planning doesn't need stage 1, so planning and fetching run at the same time (an ADK `ParallelAgent`).

---

## The plan's shape

The LLM's plan and the fallback plan use **the same schema** and go through **the same checks**. Later stages never need to know which one they got (apart from showing `source` in the brief).

```json
{
  "plan_version": 1,
  "source": "llm | fallback | user",
  "model": "gemini-pro-latest",
  "created_at": "2026-10-20T12:00:00Z",

  "topic": "AI and job losses",
  "topic_key": "ai and job losses",

  "interpretation": "Effects of AI adoption on employment levels: jobs lost, displaced or not created.",
  "assumptions": ["Covers paid employment in any country; the topic names no region."],

  "subject_terms": {
    "core":     ["ai", "artificial intelligence", "job losses"],
    "synonyms": ["automation", "unemployment", "job displacement", "layoffs", "generative ai", "workforce reduction"]
  },

  "metrics": [
    { "id": "job_loss_rate",
      "label": "Job loss rate",
      "definition": "Share of workers in a group whose jobs were eliminated, attributed to AI.",
      "measure_type": "share",
      "unit": "percent",
      "useful_for": "Comparing realised displacement across sectors or countries.",
      "confusable_with": ["ai_exposure_share"] },
    { "id": "ai_exposure_share",
      "label": "Share of jobs exposed to AI",
      "definition": "Share of jobs whose tasks could be affected by AI. A measure of potential, not of losses.",
      "measure_type": "share",
      "unit": "percent",
      "useful_for": "Sizing potential impact, not realised losses.",
      "confusable_with": ["job_loss_rate"] }
  ],

  "questions": [
    { "id": "q1",
      "text": "How many jobs have been lost or displaced due to AI so far, and in which groups?",
      "angle": "scale",
      "kind": "quantitative",
      "why_it_matters": "Headline figures vary widely depending on what is measured.",
      "metric_ids": ["job_loss_rate", "ai_exposure_share"],
      "facets": [ {"dimension": "population", "name": "sector", "examples": ["software", "healthcare"]},
                  {"dimension": "geography",  "name": "region", "examples": []} ],
      "question_terms": ["redundancies", "headcount cuts", "positions eliminated"] }
  ]
}
```

### Two kinds of terms, kept separate
| | `subject_terms` (whole plan) | `question_terms` (per question) |
|---|---|---|
| **Means** | "This text is about the topic." | "This text answers *this* question." |
| **Example** | automation, unemployment, generative AI | retraining, universal basic income (for a "responses" question) |
| **Used for** | Stage 2 relevance fallback; base score in paragraph selection | Bonus score in paragraph selection; helps stage 3 pick the `question_id` |
| **Rule** | `core` always contains the topic's own words | Must not repeat subject terms (code removes any overlap), so each list stays meaningful |

### Metrics: one standard list, referenced by ID
- Defined **once at plan level**. Questions refer to them by `metric_ids`. Stage 3 then maps every number it extracts to one of these IDs, or to `other`. That shared ID is the "standard name" stages 4–5 compare on.
- `id`: lowercase snake_case, unique. Stable names make grouping by code reliable.
- `measure_type`: `share` | `rate` | `count` | `amount` | `change` | `index` | `ratio` | `other`.
- `unit`: `percent` | `percentage_points` | `count` | `currency` | `index_points` | `years` | `other`.
  These match the claim format's `unit` and `value_kind`, so stage 5 can tell whether two numbers are comparable at all.
- `useful_for`: one line on when the metric is the right lens. It's shown in the brief next to the numbers.
- `confusable_with`: metrics a careless reader might mix up. Stage 5 uses this to say "these sources aren't contradicting each other; they measure different things", not "contradiction".

### No more assumptions than necessary
- `interpretation` is **one sentence restating the topic**. No facts, no claimed answers.
- `assumptions`: **at most 2**, and only about scope the topic leaves open (region, time frame, definition). The prompt prefers **leaving scope open** ("any country") over picking one. If the topic is ambiguous, the plan **names the ambiguity** rather than quietly resolving it.
- **Questions must be neutral, with no built-in answer.** "Has AI caused net job losses in software?", not "Why has AI destroyed software jobs?". The prompt gives this exact example.
- Checked in code: assumptions capped at 2 and at ~200 characters each; longer or extra ones are cut, and that's recorded.

---

## The steps

### Step 1: Check and normalise the topic
- Not empty, at most 300 characters. Invalid → clear error before anything runs. There's nothing sensible to fall back to.
- `topic` keeps the user's wording, with whitespace trimmed.
- `topic_key` is the normalised form, used to match against the cache:
  1. unicode normalised (NFKC) and lowercased
  2. `&` → `and`, and hyphens/slashes → spaces (so "job-losses" equals "job losses")
  3. all other punctuation removed
  4. whitespace collapsed

  So `"AI & Job-Losses!"`, `"ai  and job losses"` and `"AI and job losses."` all become `ai and job losses`.

### Step 2: Ask the model for a plan (one Pro-class call)
- The model sees **only the topic, never the sources**. Questions taken from the sources would always be "answered", which makes gap detection meaningless.
- **Asked for:** the interpretation, at most 2 assumptions, subject terms, the metric list, and 5–8 questions. Structured output enforces the schema.
- **Each question has an `angle`:**

  | Angle | Meaning |
  |---|---|
  | `scale` | size / current state (the numbers) |
  | `causes` | causes and mechanisms |
  | `affected_groups` | who is affected, and how differently |
  | `forecasts` | what's expected next |
  | `disagreements` | where experts disagree |
  | `responses` | responses / policy |
  | `other` | anything else |

  The prompt asks for a spread of angles.
- **No reworded duplicates:** the prompt says two questions are duplicates if **the same evidence would answer both**, even when they're worded differently. Example given:
  - "What % of jobs has AI eliminated?" and "How large are AI-driven job losses so far?" → duplicates.
  - "…so far?" and "…by 2030?" → not duplicates (one is evidence, the other a forecast).

  Before answering, the model is told to check every pair and merge duplicates.

### Step 3: Check the plan in code (same checks for LLM, fallback and user plans)
- **5–8 questions.** Over 8 → keep the first 8. Under 5 → problem.
- IDs renumbered `q1…qN`. Metric IDs must be unique snake_case. Every `metric_ids` entry must exist in `metrics`, and unknown ones are removed.
- **Duplicate safety net** (the prompt is the main defence; this catches what slips through):
  - Two questions with the **same angle and the same metrics**, or
  - Two questions whose content words + question terms overlap heavily (Jaccard ≥ 0.6)

  → the later one is a duplicate. It's named in the retry message ("q3 and q5 ask the same thing"). If it's still there after the retry, it's removed.
- `quantitative` questions need at least one metric. Still missing after the retry → switched to `qualitative`.
- Facet dimensions must be `population | geography | timeframe | metric | other`; anything else becomes `other`.
- **Terms:** lowercased and de-duplicated. The topic's own words are added to `subject_terms.core`. Question terms that repeat subject terms are removed. Caps: 40 subject terms, 10 per question.
- **Assumptions:** at most 2, ~200 characters each.

### Step 4: Retry once, then fall back (never crash)
1. **Problems from Step 3, or invalid JSON / API error** → one retry. The retry message lists the problems.
2. **Still unusable** (invalid JSON, or under 5 questions after fixes) → **fallback plan, same schema**:
   - `interpretation`: "Generic research plan (automatic planning failed); scope left open."
   - `assumptions`: [].
   - Six questions, one per angle, built from the topic: "What is the current scale of {topic}?", "What are the main causes or drivers of {topic}?", and so on.
   - One generic metric: `{"id": "reported_magnitude", "label": "Reported magnitude (any measure)", "measure_type": "other", "unit": "other", "useful_for": "Collecting any figure sources report", "confusable_with": []}`. It's attached to the `scale` question.
   - `subject_terms.core` = the topic's words; synonyms and question terms empty.
   - It goes **through the same Step 3 checks**, and a test makes sure it passes them.
   - `source: "fallback"`, and the brief says the research questions were generic.

### Step 5: Save, reuse safely, let a person edit
- Written to `runs/<id>/plan.json`.
- **Reuse only if it really is the same plan request.** A saved plan is reused when:
  - its `topic_key` equals the new topic's `topic_key`, **and**
  - its `plan_version` equals the code's current version (bumped when the prompt or schema changes).

  Otherwise a new plan is made, and the old one is kept as `plan.prev.json` with a warning printed. `--replan` always makes a new one.
- **`--plan-file my_plan.json`:** an analyst-edited plan. It goes through Step 3, gets `source: "user"`, and its `topic_key` must match the run's topic. If it doesn't, stop with a clear error, because silently using a plan for a different topic would be wrong.

### Step 6: Connect it to stage 2
- `clean_sources` reads `plan.json` when present.
- **Paragraph selection score** = subject-term matches (is it about the topic?) + a **bonus** for question-term matches (does it answer a question?).
- **Relevance fallback** (no LLM) uses subject terms only.

### Step 7: Command to run stage 0 on its own
`python -m brief_agent.plan --topic "AI and job losses" --run-dir runs/<id> [--replan] [--plan-file f.json]` prints:
- interpretation + assumptions
- subject terms
- the metric list (`id`, unit, `useful_for`)
- each question with angle, kind, metric IDs, facets and question terms
- `source`, and whether the plan was reused from the cache

---

## Files to create / change

```
brief_agent/
  llm.py               # + PLANNER_MODEL (env BRIEF_PLANNER_MODEL, default gemini-pro-latest)
  plan/
    __init__.py
    __main__.py        # Step 7
    models.py          # pydantic: Plan, Question, Metric, Facet, SubjectTerms (also the LLM response schema)
    topic.py           # Step 1: validation + topic_key normalisation
    validate.py        # Step 3: checks + fixes, returns (plan, problems)
    planner.py         # Steps 2, 4, 5: prompt, retry, fallback, cache/plan-file
  clean/pipeline.py    # read plan.json → subject + question terms
  clean/select.py      # score = subject terms + question-term bonus
  clean/relevance.py   # fallback uses subject terms
.env.example / .env.local   # + BRIEF_PLANNER_MODEL
tests/test_plan.py
```

## Not in this stage (on purpose)
- No looking at sources while planning.
- No interactive clarifying questions: ambiguity goes into `interpretation`, and `--plan-file` allows an override.
- No changing the plan after reading sources: off-plan claims go to `question_id: "other"` in stage 3.

---

## How we'll check it works

### Unit tests (fake LLM, no API key)
| Case | Pass condition |
|---|---|
| Valid plan | Parsed, IDs `q1…qN`, metric references valid, saved with `source: "llm"`. |
| **Reworded duplicate** ("What % of jobs has AI eliminated?" / "How large are AI-driven job losses so far?", same angle and metrics) | Named in the retry message; still present after the retry → second one removed. |
| Not a duplicate ("…so far?" vs "…by 2030?", different angles) | Both kept. |
| Question refers to an unknown metric ID | Reference removed; a quantitative question left with none → named in the retry. |
| Duplicate metric IDs / not snake_case | Problem reported; fixed or dropped. |
| Question terms repeating subject terms | Overlap removed from the question terms. |
| Topic words missing from `subject_terms.core` | Added back. |
| 4 assumptions, one 500 chars long | 2 kept, each ≤ 200 chars. |
| LLM returns invalid JSON twice | Fallback plan; **it passes the same checks**; same fields as an LLM plan; `source: "fallback"`. |
| **Cache: same topic, different punctuation/spacing/case** (`"AI & Job-Losses!"` vs `"ai and job losses"`) | Plan reused; LLM not called. |
| Cache: different topic | New plan; old one saved as `plan.prev.json`. |
| Cache: older `plan_version` | New plan. |
| `--plan-file` for a different topic | Clear error. |
| `--plan-file` with a bad facet dimension | Loaded; dimension → `other`; `source: "user"`. |
| Empty / 1,000-char topic | Error before any LLM call. |
| **Stage 2 integration** | A paragraph saying "unemployment" but not "job" is selected only once the plan's subject terms are used; a paragraph matching a question term outranks one that only matches subject terms. |

### With a real API key
- "AI and job losses":
  - 5–8 questions, at least 4 different angles, no two answerable by the same evidence (checked by reading).
  - Metrics include both a loss measure and an exposure measure, marked `confusable_with` each other.
  - At most 2 assumptions, and none that narrow the scope without reason.
- "Apple layoffs": the interpretation names the ambiguity rather than resolving it silently.
- Check the `gemini-pro-latest` alias exists. If not, set the correct name in `.env.example`.
- Re-run stage 2 on `runs/stage2-dev` with the plan: compare which Wikipedia paragraphs are selected against the current run.

## Decided
1. **Planner model:** Pro-class Gemini (`BRIEF_PLANNER_MODEL`).
2. **5–8 questions**, with a spread of angles and no reworded duplicates.
3. **`--plan-file` included**; it must match the run's topic.
4. **The fallback plan has the same schema** and passes the same checks.
5. **Subject terms and question terms are kept separate**, with different jobs.
6. **Metrics are a standard list at plan level** (ID, unit, measure type, when useful, what they're confused with), referenced by ID.
7. **Minimal assumptions:** at most 2, scope-only, neutral questions.
8. **The cache is matched on the normalised topic** and the plan version.

Rough time: ~30 minutes.
