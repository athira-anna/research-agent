# Stage 6: The brief, and wrapping up the project

> **Superseded in part (later decision):** the brief is now written as `brief.html` only (no Markdown or JSON), and the agent is run with `adk web agents` / `adk run agents/research_brief` instead of `python -m brief_agent run`. See the README.

## Why this stage exists

Everything so far produces JSON. Stage 6 turns `analysis.json` into **the thing an analyst or PM actually opens**: a brief that leads with what matters, separates agreement from disagreement, shows *why* sources differ, says what nobody covers, and backs every statement with a checkable citation.

The same principle applies as before: **code writes the brief, and the LLM writes only the short "bottom line" at the top, which code then checks.** Every other sentence comes straight from findings that are already checked, so nothing new and unchecked gets added at the last step.

**Input:** `analysis.json` (+ `claims.json`, `sources.json`, `plan.json`).
**Output:** `brief.md` (the deliverable), `brief.html` (the same brief as a self-contained page for reading), and `brief.json` (the same content, structured).

---

## Step 6.1: The brief's layout (most useful first)

```
# AI and job losses: research brief
Topic as understood · assumptions · 3 of 5 sources used · generated <date>

## Bottom line                         ← 3–5 bullets, LLM-written, code-checked (6.3)
## Where sources agree                 ← consensus findings, strongest first
## Where sources disagree              ← contested (with balance), then unresolved (with reasons)
## Same question, different slices     ← complementary findings: by place / group / part vs whole / over time
## Don't compare these                 ← different_measures (e.g. exposure ≠ loss), not_comparable (forecast vs measured)
## Claims from a single origin         ← outliers: only one study/organisation says it
## What no source covers               ← gaps: unanswered/thin questions, uncovered facets, metrics with no figures
## Also raised by sources              ← emerging claims outside the plan
## Sources                             ← table: fetch / processing / usability / claims / coverage, with reasons
## How this brief was made             ← models used, fallbacks, checks, dropped-claim counts, caveats
## Evidence                            ← numbered citations: source, block ID, page, exact quote, URL
```

**Each finding is shown the same way:**
```
### Share of jobs at high risk of automation — CONTESTED (3 origins vs 1) · strength: moderate
- **~half at risk (47–54%)**: Frey & Osborne [1], PwC [2], Bruegel [3]
- **few at risk (9%)**: OECD [4]
- Why they may differ: varies by method named in the quotes (occupation-level vs task-level); different years (2013–2017)
- Caveats: all origins reach us through one source (a secondary summary)
```
- **Labels are always explained in plain words,** never shown as raw codes. For example, `complementary (part_vs_whole)` becomes "the US figure is part of the global one".
- **Numbers are shown with their meaning:** "level 10% (from 5%)", "+5 points", "relative +100%".

## Step 6.2: Citations
- Every claim cited in the brief gets a number `[n]`, in order of first appearance.
- The **Evidence** section lists each one: `[n] Source title (site) · block s1-p036 · p. 4 · "exact quote" · URL`.
  - Quotes are the checked quotes from stage 3, so they really are in the source.
  - A reader can find the passage, and `runs/<id>/clean/s1.md` has every block marked with its ID.
- Origin and source are both shown: "PwC (via Wikipedia)". The reader can see when a figure is second-hand.

## Step 6.3: The bottom line (the only LLM-written text)
- **One call** (the reasoning model chain), given only the findings, gaps and source summary from `analysis.json`. No source text.
- **Asked for** 3–5 bullets, each one:
  - stating a finding in plain words
  - naming its label: agree / disagree / gap
  - **listing the finding IDs it's based on**
- **Checked by code:**
  - Every bullet must cite at least one existing finding ID, or it's dropped.
  - **Every number in a bullet must appear in the claims of the findings it cites,** using the stage 3 number matching. A bullet with an unbacked number is dropped. This is the same "no new numbers" rule as stage 3, applied to the summary.
  - A bullet calling something "consensus" or "contradiction" must cite a finding with that label.
  - Fewer than 2 valid bullets → the fallback.
- **Fallback (LLM down, or too few valid bullets):** template bullets from the top findings by strength, e.g. "Sources disagree on X (3 origins vs 1)." The brief marks the bottom line as "auto-generated from findings".

## Step 6.4: Ordering and selection
- Within each section: strength (strong → weak), then number of origins, then question order.
- Findings that are nothing but "unjudged" go into a short "addressed but not assessed" list, not the main sections.
- Single-origin findings are capped at ~10 in the main text; the rest stay in `brief.json`.

## Step 6.5: Honesty section ("How this brief was made")
Generated from run data, not written by hand:
- Sources: fetched / read / used / excluded, each with a reason (paywalled, bot-blocked, off-topic, duplicate…).
- Per source: claims kept, dropped by reason, and how much of the source was read (coverage).
- Models actually used per stage, and any fallbacks (e.g. "Pro unavailable; Flash-Lite used").
- Plan source: `llm` / `fallback` / `user`.
- What the checks do and don't guarantee: "Quotes are verified to be in the source and checked to support the claim; claims are not verified as true."

---

## Wrap-up

### W1: One command for the whole pipeline
`python -m brief_agent run --topic "…" --urls URL1 URL2 …` (or `--input inputs/ai_jobs.json`), which:
- runs **stage 0 (plan) and stage 1 (fetch) in parallel**, then stages 2 → 6
- writes everything to `runs/<timestamp>/` and prints the path to `brief.md` at the end
- shows a one-line progress update per stage
- **never stops halfway because one source failed.** Stage-level failures are already handled. A run only aborts if *no* source is usable, and then it still writes a brief explaining why.

How the stages are orchestrated: see Decision 1.

### W2: Sample inputs
`inputs/` holds 2–3 ready-to-run topic + URL sets, including the AI-and-jobs set used for the demo run. Each set mixes deliberately: an easy page, a JS-heavy page, a PDF, and one that fails (paywall or bot wall), so graceful failure shows up in every demo.

### W3: Submission files
| File | Content |
|---|---|
| `README.md` | Quickstart (setup, one command, where the output is), the stages, configuration, tests. Already mostly written; adds the `run` command and a sample brief excerpt. |
| `NOTES.md` | **Half a page:** overall approach, what I'd do differently for production, 2-hour-budget trade-offs. Draws on `DECISIONS.log` and the spot-check review. |
| `prompts.md` | Already curated (15 entries). Add the final entries via the `log-prompts` skill. |
| `ai-log/` | Auto-captured prompts and transcripts (hooks). Nothing to do. |
| `docs/loom-outline.md` | A 5-minute script for the Loom: demo, 2–3 key decisions, one improvement, one thing that failed. Taken from `DECISIONS.log` so it's concrete. |
| `docs/plans/`, `docs/reviews/` | Already there: stage plans and the stage 3 spot check. |

### W4: Final checks
- Full test suite (including real-LLM tests).
- One clean end-to-end run on each sample input; read each brief by hand.
- Spot-check every `contested` and `consensus` finding in the demo brief against its quotes. The accuracy goes into NOTES.md, like the stage 3 spot check.
- `pip install -r requirements.txt` in a fresh venv + `playwright install chromium`, to confirm the README setup works.

### W5: Git and GitHub (needs your go-ahead)
The folder isn't a git repository yet. The submission needs a GitHub link: `git init`, a first commit, and creating and pushing the repo. Commits and pushes are only done when you ask. `.env.local` (your key) is already in `.gitignore`.

---

## Files to create

```
brief_agent/
  brief/
    __init__.py, __main__.py
    render.py        # 6.1, 6.2, 6.4, 6.5: Markdown from analysis.json (pure code)
    wording.py       # labels, patterns, reasons and numbers → plain English
    bottom_line.py   # 6.3: LLM call + checks + fallback
  __main__.py        # W1: `python -m brief_agent run …`
  pipeline.py        # W1: the end-to-end orchestration
inputs/              # W2
NOTES.md, docs/loom-outline.md   # W3
tests/test_brief.py
```

## How we'll check it works
| Case | Expected |
|---|---|
| Bottom-line bullet with a number not in its cited findings | Bullet dropped |
| Bullet citing an unknown finding ID / no ID | Dropped |
| Bullet says "consensus" but cites a contested finding | Dropped |
| LLM down, or < 2 valid bullets | Template bottom line, marked as auto-generated |
| Every `[n]` in the text | Has an Evidence entry whose quote is in the source's blocks |
| Each label/pattern/reason | Has a plain-English wording (no raw codes in `brief.md`) |
| Run where every source fails | Still writes a brief: "no usable sources" + the source table with reasons |
| Excluded sources | Appear in the Sources table with their reason |
| `brief.json` | Same findings and citations as `brief.md` |

## Decisions for you
1. **Orchestration:** wire the stages into **Google ADK** now (a `SequentialAgent` with a `ParallelAgent` for plan + fetch; each stage a custom agent), as planned earlier? Or a **plain async Python runner** now, with ADK described in NOTES as the production step? ADK 2.x is new to me, so wiring it is ~30–45 min of uncertain work.
2. **Output format: decided: Markdown + JSON + HTML.** `brief.html` is a self-contained page: inline CSS, light/dark, citations as links to the Evidence section, no external scripts. It's rendered from the same data as `brief.md`, so the two can't drift apart.

Rough time: stage 6 ~40 min; wrap-up ~30 min (+ ADK if chosen).
